"""Dry-run validation. Pure reading: this module never writes to the database.
`validate_import` is called twice, once for the dry run and again at commit time against the
then-current data, so a commit never relies on a stale report."""
import re
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from tracker.importing import clean_amount, clean_date
from tracker.models import Estate, ObligationType, Sale

from .mapping import FIELD_KEYS, REQUIRED, mapping_problems, normalize_status_value

MAX_ISSUES = 5000
_BIG = Decimal(10) ** 12


def norm(text):
    return re.sub(r"\s+", " ", str(text or "")).strip().casefold()


def as_text(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return re.sub(r"\s+", " ", str(value)).strip()


def money(value):
    d = clean_amount(value)
    if abs(d) >= _BIG:
        raise ValueError("amount is too large")
    return d.quantize(Decimal("0.01"))


def _empty_report(problems=None):
    return {
        "summary": {"total_rows": 0, "to_import": 0, "unchanged": 0, "conflicts": 0, "errors": 0, "warnings": 0,
                    "blank_rows": 0, "new_estates": [], "buyers_to_create": 0, "can_commit": False},
        "unmapped_statuses": [], "mapping_problems": problems or [], "issues": [], "issues_truncated": False,
    }


def validate_import(batch, today=None):
    """Return (report, prepared_rows). `prepared_rows` are fully parsed rows that WOULD be imported."""
    today = today or timezone.localdate()
    headers = batch.headers
    mapping = batch.mapping or {}
    types = {t.name: t for t in ObligationType.objects.filter(company_id=batch.company_id, is_active=True)}
    problems = mapping_problems(mapping, headers, set(types))
    if problems:
        return _empty_report(problems), []

    idx = {h: i for i, h in enumerate(headers)}
    col = {f: idx[mapping[f]] for f in FIELD_KEYS if mapping.get(f)}
    docs = {t: idx[h] for t, h in (mapping.get("documents") or {}).items()}
    status_map = batch.status_mapping or {}

    existing_estates = {norm(e.name) for e in Estate.objects.filter(company_id=batch.company_id)}
    active = {}
    for s in Sale.objects.filter(company_id=batch.company_id, status=Sale.Status.ACTIVE).select_related("plot__estate", "buyer"):
        active[(norm(s.plot.estate.name), norm(s.plot.plot_number))] = (norm(s.buyer.full_name), s.price, s.buyer.full_name)

    issues, prepared, unmapped = [], [], {}
    counts = {"unchanged": 0, "conflicts": 0, "errors": 0, "warnings": 0}
    seen, new_estates = {}, {}

    def add(row_n, level, column, message, value=None):
        key = {"error": "errors", "warning": "warnings", "conflict": "conflicts"}[level]
        counts[key] += 1
        if len(issues) < MAX_ISSUES:
            issues.append({"row": row_n, "level": level, "column": column, "message": message,
                           "value": "" if value is None else str(value)[:80]})

    for row in batch.rows:
        n, vals = row["n"], row["v"]
        raw = {f: vals[i] for f, i in col.items()}
        errors_before = counts["errors"]

        # --- text fields ---
        estate = as_text(raw.get("estate"))
        plot = as_text(raw.get("plot_number"))
        buyer = as_text(raw.get("buyer_name"))
        for field, text, limit in (("estate", estate, 200), ("plot_number", plot, 50), ("buyer_name", buyer, 200)):
            label = mapping[field]
            if not text:
                add(n, "error", label, "Required value is missing.")
            elif len(text) > limit:
                add(n, "error", label, f"Too long (maximum {limit} characters).", text)

        # --- money ---
        price = paid = None
        try:
            price = money(raw.get("price"))
            if price <= 0:
                add(n, "error", mapping["price"], "Price must be greater than zero.", raw.get("price"))
                price = None
        except ValueError as exc:
            add(n, "error", mapping["price"], f"Invalid price: {exc}.", raw.get("price"))
        if raw.get("amount_paid") is None or as_text(raw.get("amount_paid")) == "":
            paid = Decimal("0.00")
            add(n, "warning", mapping["amount_paid"], "Amount paid is blank; treated as 0.")
        else:
            try:
                paid = money(raw.get("amount_paid"))
                if paid < 0:
                    add(n, "error", mapping["amount_paid"], "Amount paid cannot be negative.", raw.get("amount_paid"))
                    paid = None
            except ValueError as exc:
                add(n, "error", mapping["amount_paid"], f"Invalid amount paid: {exc}.", raw.get("amount_paid"))
        if price is not None and paid is not None and paid > price:
            add(n, "warning", mapping["amount_paid"], "Amount paid is more than the price; treated as fully paid.", raw.get("amount_paid"))

        # --- dates ---
        sale_date = fully_paid_on = None
        if "sale_date" in col and as_text(raw.get("sale_date")):
            try:
                sale_date = clean_date(raw["sale_date"])
                if sale_date.year < 1990 or sale_date > today + timedelta(days=1):
                    add(n, "error", mapping["sale_date"], "Date is implausible (before 1990 or in the future).", raw["sale_date"])
                    sale_date = None
            except ValueError as exc:
                add(n, "error", mapping["sale_date"], f"Invalid date: {exc}.", raw["sale_date"])
        if "fully_paid_on" in col and as_text(raw.get("fully_paid_on")):
            try:
                fully_paid_on = clean_date(raw["fully_paid_on"])
                if fully_paid_on.year < 1990 or fully_paid_on > today + timedelta(days=1):
                    add(n, "error", mapping["fully_paid_on"], "Date is implausible (before 1990 or in the future).", raw["fully_paid_on"])
                    fully_paid_on = None
            except ValueError as exc:
                add(n, "error", mapping["fully_paid_on"], f"Invalid date: {exc}.", raw["fully_paid_on"])

        is_full = price is not None and paid is not None and paid >= price
        if sale_date is None and counts["errors"] == errors_before:
            sale_date = fully_paid_on if (is_full and fully_paid_on) else today
            add(n, "warning", mapping.get("sale_date", "(sale date)"),
                "Sale date is missing; ageing for this buyer will start from " +
                ("the date fully paid." if (is_full and fully_paid_on) else "today."))
        if fully_paid_on and not is_full:
            add(n, "warning", mapping["fully_paid_on"], "Date fully paid ignored: the buyer has not paid in full.", raw.get("fully_paid_on"))
            fully_paid_on = None
        if is_full and fully_paid_on is None and sale_date and counts["errors"] == errors_before:
            add(n, "warning", mapping.get("fully_paid_on", "(date fully paid)"),
                "Date fully paid is unknown; using the sale date, so documents may look older than they are.")
        if is_full and fully_paid_on and sale_date and fully_paid_on < sale_date:
            add(n, "warning", mapping["fully_paid_on"], "Date fully paid is before the sale date; using the sale date.")
            fully_paid_on = sale_date

        # --- document statuses (always scanned so every unmapped value is reported once) ---
        statuses = {}
        for tname, i in docs.items():
            key = normalize_status_value(vals[i])
            mapped = status_map.get(key)
            if mapped is None:
                entry = unmapped.setdefault(key, {"key": key, "example": "" if vals[i] is None else str(vals[i]), "count": 0})
                entry["count"] += 1
                continue
            if mapped == "not_started":
                continue
            if types[tname].trigger == ObligationType.Trigger.ON_FULL_PAYMENT and not is_full:
                add(n, "warning", mapping["documents"][tname],
                    f"'{tname}' is marked as done/in progress but the buyer has not paid in full; status ignored.", vals[i])
                continue
            statuses[tname] = mapped

        # --- duplicates, conflicts ---
        status = "ok"
        if counts["errors"] > errors_before:
            status = "error"
        elif estate and plot:
            key = (norm(estate), norm(plot))
            if key in seen:
                add(n, "error", mapping["plot_number"], f"Duplicate of row {seen[key]}: same estate and plot.", plot)
                status = "error"
            else:
                seen[key] = n
                current = active.get(key)
                if current:
                    if current[0] == norm(buyer) and current[1] == price:
                        counts["unchanged"] += 1
                        status = "unchanged"
                    else:
                        add(n, "conflict", mapping["plot_number"],
                            f"Plot is already sold to {current[2]} (price {current[1]}); not imported.", plot)
                        status = "conflict"

        if status == "ok":
            if norm(estate) not in existing_estates:
                new_estates.setdefault(norm(estate), estate)
            prepared.append({
                "row": n, "estate": estate, "estate_key": norm(estate), "plot": plot, "plot_key": norm(plot),
                "buyer_name": buyer, "phone": as_text(raw.get("phone")), "email": as_text(raw.get("email")),
                "price": price, "amount_paid": paid, "sale_date": sale_date, "fully_paid_on": fully_paid_on,
                "statuses": statuses,
            })

    unmapped_list = sorted(unmapped.values(), key=lambda e: (-e["count"], e["key"]))
    report = {
        "summary": {
            "total_rows": len(batch.rows), "to_import": len(prepared), "unchanged": counts["unchanged"],
            "conflicts": counts["conflicts"], "errors": counts["errors"], "warnings": counts["warnings"],
            "blank_rows": 0, "new_estates": list(new_estates.values()), "buyers_to_create": len(prepared),
            "can_commit": bool(prepared) and not unmapped_list,
        },
        "unmapped_statuses": unmapped_list, "mapping_problems": [], "issues": issues,
        "issues_truncated": (counts["errors"] + counts["warnings"] + counts["conflicts"]) > len(issues),
    }
    return report, prepared
