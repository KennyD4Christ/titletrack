import re

from tracker.models import Obligation

from .errors import MappingError

BLANK_KEY = "(blank)"

# key, label, required
FIELDS = [
    ("estate", "Estate", True),
    ("plot_number", "Plot / unit number", True),
    ("buyer_name", "Buyer name", True),
    ("price", "Price", True),
    ("amount_paid", "Amount paid to date", True),
    ("sale_date", "Sale date", False),
    ("fully_paid_on", "Date fully paid", False),
    ("phone", "Buyer phone", False),
    ("email", "Buyer email", False),
]
FIELD_KEYS = [k for k, _, _ in FIELDS]
REQUIRED = [k for k, _, req in FIELDS if req]

SYNONYMS = {
    "estate": ["estate", "estatename", "development", "project", "scheme", "layout", "site"],
    "plot_number": ["plot", "plotno", "plotnumber", "plotid", "unit", "unitno", "unitnumber", "property"],
    "buyer_name": ["buyer", "buyername", "customer", "customername", "client", "clientname", "subscriber", "allottee", "name"],
    "price": ["price", "plotprice", "sellingprice", "totalprice", "contractprice", "salesprice", "purchaseprice", "cost"],
    "amount_paid": ["amountpaid", "paid", "totalpaid", "paidtodate", "totalpayment", "paymentmade", "amtpaid", "payment"],
    "sale_date": ["saledate", "dateofsale", "purchasedate", "subscriptiondate", "datesold", "dateofpurchase", "date"],
    "fully_paid_on": ["fullypaidon", "datefullypaid", "paymentcompleted", "completiondate", "datecompleted",
                      "fullpaymentdate", "datepaidfully", "paymentcompletiondate"],
    "phone": ["phone", "phonenumber", "mobile", "tel", "telephone", "gsm", "mobilenumber"],
    "email": ["email", "emailaddress"],
}
STOPWORDS = {"of", "and", "the", "for", "letter", "plan"}


def normalize_header(h):
    return re.sub(r"[^a-z0-9]", "", str(h).casefold())


def suggest_mapping(headers, type_names):
    """Best guess only; the user always confirms."""
    norm = {h: normalize_header(h) for h in headers}
    used, mapping = set(), {}
    for field in FIELD_KEYS:
        for synonym in SYNONYMS[field]:
            hit = next((h for h in headers if h not in used and norm[h] == synonym), None)
            if hit:
                mapping[field] = hit
                used.add(hit)
                break
    documents = {}
    for tname in type_names:
        words = [w for w in re.findall(r"[a-z0-9]+", tname.casefold()) if w not in STOPWORDS and len(w) > 2]
        first = words[0] if words else normalize_header(tname)
        hit = next((h for h in headers if h not in used and first in norm[h]), None)
        if hit:
            documents[tname] = hit
            used.add(hit)
    if documents:
        mapping["documents"] = documents
    return mapping


def validate_mapping(mapping, headers, type_names):
    """Return a cleaned mapping or raise MappingError with human-readable messages."""
    problems = mapping_problems(mapping, headers, type_names)
    if problems:
        raise MappingError(problems)
    cleaned = {k: mapping[k] for k in FIELD_KEYS if mapping.get(k)}
    docs = {t: h for t, h in (mapping.get("documents") or {}).items() if h}
    if docs:
        cleaned["documents"] = docs
    return cleaned


def mapping_problems(mapping, headers, type_names=None):
    problems = []
    if not isinstance(mapping, dict):
        return ["Mapping must be an object."]
    known = set(headers)
    unknown = [k for k in mapping if k not in FIELD_KEYS and k != "documents"]
    if unknown:
        problems.append(f"Unknown fields: {', '.join(sorted(unknown))}.")
    used = {}
    for field in FIELD_KEYS:
        header = mapping.get(field)
        if not header:
            if field in REQUIRED:
                problems.append(f"'{dict((k, l) for k, l, _ in FIELDS)[field]}' must be mapped to a column.")
            continue
        if header not in known:
            problems.append(f"Column '{header}' does not exist in the file.")
        elif header in used:
            problems.append(f"Column '{header}' is used for both '{used[header]}' and '{field}'.")
        else:
            used[header] = field
    docs = mapping.get("documents") or {}
    if not isinstance(docs, dict):
        problems.append("'documents' must map obligation type names to columns.")
        docs = {}
    for tname, header in docs.items():
        if not header:
            continue
        if type_names is not None and tname not in type_names:
            problems.append(f"'{tname}' is not an active obligation type.")
        if header not in known:
            problems.append(f"Column '{header}' does not exist in the file.")
        elif header in used:
            problems.append(f"Column '{header}' is used for both '{used[header]}' and '{tname}'.")
        else:
            used[header] = tname
    return problems


# ---------- status values ----------

def normalize_status_value(raw):
    if raw is None or str(raw).strip() == "":
        return BLANK_KEY
    return re.sub(r"\s+", " ", str(raw).strip().casefold())


_COMPLETED = {"done", "yes", "y", "issued", "delivered", "completed", "complete", "received", "collected",
              "signed", "registered", "available", "ok", "true"}
_NOT_APPLICABLE = {"na", "n/a", "not applicable", "not required", "none required"}
_EXTERNAL_WORDS = ("lawyer", "solicitor", "registry", "ministry", "agency", "external", "submitted", "processing",
                   "government", "surveyor", "with ")
_IN_PROGRESS_WORDS = ("in progress", "inprogress", "ongoing", "started", "preparing", "drafting", "working", "review")
_NOT_STARTED = {BLANK_KEY, "no", "n", "pending", "not yet", "nil", "none", "false", "not started", "outstanding"}


def suggest_status(key):
    """Guess an obligation status for a normalized cell value; None when unsure."""
    if key in _NOT_APPLICABLE:
        return Obligation.Status.NOT_APPLICABLE.value
    if key in _COMPLETED:
        return Obligation.Status.COMPLETED.value
    if key in _NOT_STARTED:
        return Obligation.Status.NOT_STARTED.value
    if any(w in key for w in _EXTERNAL_WORDS):
        return Obligation.Status.AWAITING_EXTERNAL.value
    if any(w in key for w in _IN_PROGRESS_WORDS):
        return Obligation.Status.IN_PROGRESS.value
    return None


def distinct_status_values(batch):
    """Every distinct value found in the mapped document columns, with a count and a suggestion."""
    docs = (batch.mapping or {}).get("documents") or {}
    index = {h: i for i, h in enumerate(batch.headers)}
    found = {}
    for header in docs.values():
        i = index.get(header)
        if i is None:
            continue
        for row in batch.rows:
            raw = row["v"][i]
            key = normalize_status_value(raw)
            entry = found.setdefault(key, {"key": key, "example": "" if raw is None else str(raw), "count": 0})
            entry["count"] += 1
    for entry in found.values():
        entry["suggested"] = suggest_status(entry["key"])
    return sorted(found.values(), key=lambda e: (-e["count"], e["key"]))


def validate_status_mapping(status_mapping):
    valid = {s.value for s in Obligation.Status}
    if not isinstance(status_mapping, dict):
        raise MappingError(["status_mapping must be an object."])
    bad = [f"'{v}' is not a valid status for '{k}'." for k, v in status_mapping.items() if v not in valid]
    if bad:
        raise MappingError(bad)
    return {normalize_status_value(k): v for k, v in status_mapping.items()}
