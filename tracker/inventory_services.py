"""Estate and plot inventory: registering estates and plots without a sale, and keeping them tidy.

Every write goes through this module, as every sales write goes through tracker/services.py. The views (added
later) check the role; this module checks the company and enforces the rules, and writes the audit trail.

What keeps duplicates out is the DATABASE, not this module: Estate.name_key and Plot.plot_key are unique per
company and per estate (migrations 0012-0014), and every writer (this module, add_sale, the importer) goes
through them. The checks here exist to give a clear answer before the database has to refuse, and an
IntegrityError that still happens (a writer got in between our check and our insert) is turned into the same
answer. Nothing here relies on locking out other writers. The one lock taken is on the estate row, with
no_key=True, and it only serialises two registrations in the same estate so that the plot cap is counted once at a
time. It protects no invariant.

Plots and sales share one row-lock protocol. rename_plot and remove_plot lock the plot FOR UPDATE; create_sale
(tracker/services.py) locks it FOR NO KEY UPDATE and re-reads it. They conflict, so a sale is recorded either before
a rename or removal (which then sees it) or not at all (it finds the plot renamed or gone: PlotChanged).

Errors, in the same shape as tracker/services.py:
- ValueError                 bad input (the API answers 400)
- InventoryConflict          valid input, but not possible in the current state (409): a duplicate, the cap, a
                             stale preview, a removal that history forbids
- PermissionDenied           another company's record
"""
import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError

from .canonical import canonical_key
from .models import Estate, InventoryEvent, Plot, Sale

ESTATE_NAME_MAX = Estate._meta.get_field("name").max_length
LOCATION_MAX = Estate._meta.get_field("location").max_length
PLOT_LABEL_MAX = Plot._meta.get_field("plot_number").max_length
BULK_MAX_LABELS = 500           # labels in one bulk request, after ranges are expanded
RANGE_MAX_PAD = 10
REASON_MIN = 3                  # the same minimum as a cancellation reason

_WHITESPACE = re.compile(r"\s+")


def max_plots_per_estate():
    """The registration cap (settings.INVENTORY_MAX_PLOTS_PER_ESTATE, 3,000 unless configured)."""
    return int(getattr(settings, "INVENTORY_MAX_PLOTS_PER_ESTATE", 3000))


# ---------- errors ----------

class InventoryConflict(Exception):
    """The request is valid, but cannot be done in the current state."""


class DuplicateEstate(InventoryConflict):
    def __init__(self, estate):
        self.estate = estate
        super().__init__(
            f"An estate called {estate.name!r} already exists. Estate names are compared ignoring letter case "
            "and extra spaces.")


class DuplicatePlot(InventoryConflict):
    def __init__(self, plot):
        self.plot = plot
        super().__init__(
            f"Plot {plot.plot_number!r} already exists in {plot.estate.name}. Plot numbers are compared ignoring "
            "letter case and extra spaces.")


class PlotCapExceeded(InventoryConflict):
    def __init__(self, limit, existing, requested):
        self.limit, self.existing, self.requested = limit, existing, requested
        super().__init__(
            f"An estate can hold at most {limit} registered plots. This one has {existing}, so {requested} more "
            "cannot be registered.")


class RemovalBlocked(InventoryConflict):
    """The plot has history that must be kept."""


class StalePreview(InventoryConflict):
    """What the commit would do is not what the preview showed. `.preview` is the fresh preview."""

    def __init__(self, preview):
        self.preview = preview
        super().__init__("The estate has changed since this preview, so nothing was registered. "
                         "Review the new preview and confirm again.")


class RegistrationRace(InventoryConflict):
    """Another writer created one of the plots between the final check and the insert. Nothing was registered."""

    def __init__(self):
        super().__init__("Another change added one of these plots while they were being registered, so nothing "
                         "was registered. Preview again.")


# ---------- input rules ----------

def collapse(value):
    """Whitespace collapsed to single spaces and stripped; letter case kept (the stored, displayed form)."""
    return _WHITESPACE.sub(" ", str(value or "")).strip()


def _invisible_characters(text):
    """Control and invisible format characters (zero-width space, bidi overrides, soft hyphen...). Pasted text can
    carry them; they look like nothing, escape the case-and-spacing comparison and make two labels that look
    identical differ. Whitespace of every kind has already been collapsed by the time this is called."""
    return sorted({f"U+{ord(ch):04X}" for ch in text if unicodedata.category(ch) in ("Cc", "Cf", "Cs")})


def _clean(value, label, limit, required=True):
    text = collapse(value)
    if not text:
        if required:
            raise ValueError(f"{label} is required.")
        return ""
    if len(text) > limit:
        raise ValueError(f"{label} is too long (maximum {limit} characters).")
    bad = _invisible_characters(text)
    if bad:
        raise ValueError(f"{label} contains an invisible or control character ({', '.join(bad)}). "
                         "Retype it instead of pasting.")
    return text


def clean_estate_name(value):
    return _clean(value, "Estate name", ESTATE_NAME_MAX)


def clean_location(value):
    return _clean(value, "Location", LOCATION_MAX, required=False)


def clean_plot_label(value):
    return _clean(value, "Plot number", PLOT_LABEL_MAX)


def _reason(value):
    text = (value or "").strip()
    if len(text) < REASON_MIN:
        raise ValueError(f"A reason of at least {REASON_MIN} characters is required.")
    return text


# ---------- shared helpers ----------

def _check_actor(actor, company):
    if actor.company_id != company.pk:
        raise PermissionDenied("You belong to another company.")


def _own(actor, company_id, what):
    if actor.company_id != company_id:
        raise PermissionDenied(f"{what} belongs to another company.")


def _log(company_id, action, actor, *, estate=None, plot=None, estate_name="", plot_label="", changes=None, note=""):
    return InventoryEvent.objects.create(
        company_id=company_id, estate=estate, plot=plot,
        estate_name=estate_name or (estate.name if estate else ""),
        plot_label=plot_label or (plot.plot_number if plot else ""),
        actor=actor, action=action, changes=changes or {}, note=note)


def _fetch_estate(estate, company, lock=False):
    """The estate row, checked to belong to `company`. With lock=True the row is locked FOR NO KEY UPDATE, which
    serialises registrations in this estate (and nothing else: inserting a plot or a sale takes only a key-share
    lock on it)."""
    queryset = Estate.objects.select_for_update(no_key=True) if lock else Estate.objects
    found = queryset.get(pk=estate.pk)
    if found.company_id != company.pk:
        raise PermissionDenied("Estate belongs to another company.")
    return found


def plot_has_sales(plot):
    """True if the plot has EVER had a sale: active or cancelled."""
    return Sale.objects.filter(plot=plot).exists()


def estate_has_sales(estate):
    return Sale.objects.filter(plot__estate=estate).exists()


# Phase 3 (site plans) adds one more reason a rename needs a reason and a plot cannot be removed: a mapped region.
# It joins plot_has_sales / estate_has_sales and the two checks below; nothing else here changes.


# ---------- estates ----------

@transaction.atomic
def create_estate(company, actor, *, name, location=""):
    """Register an estate. Raises ValueError, DuplicateEstate (the estate that already has this name is
    `.estate`), PermissionDenied."""
    _check_actor(actor, company)
    name, location = clean_estate_name(name), clean_location(location)
    key = canonical_key(name)
    existing = Estate.objects.filter(company=company, name_key=key).first()
    if existing is not None:
        raise DuplicateEstate(existing)
    try:
        with transaction.atomic():
            estate = Estate.objects.create(company=company, name=name, location=location)
    except IntegrityError:                         # a writer created it between our check and our insert
        existing = Estate.objects.filter(company=company, name_key=key).first()
        if existing is None:
            raise
        raise DuplicateEstate(existing) from None
    _log(company.pk, InventoryEvent.Action.ESTATE_CREATED, actor, estate=estate,
         changes={"name": [None, name], "location": [None, location]})
    return estate


@transaction.atomic
def edit_estate(estate, actor, *, name=None, location=None, reason=""):
    """Change an estate's name and/or location. Only the fields passed are changed.

    Renaming an estate that has sales history needs a reason: spreadsheet imports match estates by name, so a
    rename changes how the next import is read. Raises ValueError (bad input, nothing to change, reason missing),
    DuplicateEstate, PermissionDenied."""
    locked = Estate.objects.select_for_update().get(pk=estate.pk)
    _own(actor, locked.company_id, "Estate")
    changes = {}
    if name is not None:
        new_name = clean_estate_name(name)
        if new_name != locked.name:
            changes["name"] = [locked.name, new_name]
    if location is not None:
        new_location = clean_location(location)
        if new_location != locked.location:
            changes["location"] = [locked.location, new_location]
    if not changes:
        raise ValueError("Nothing to change.")
    note = (reason or "").strip()
    if "name" in changes:
        if estate_has_sales(locked):
            note = _reason(reason)
        clash = (Estate.objects.filter(company_id=locked.company_id, name_key=canonical_key(changes["name"][1]))
                 .exclude(pk=locked.pk).first())
        if clash is not None:
            raise DuplicateEstate(clash)
    for column, (_, new) in changes.items():
        setattr(locked, column, new)
    try:
        with transaction.atomic():
            locked.save(update_fields=list(changes))            # a changed name brings its key with it
    except IntegrityError:
        clash = (Estate.objects.filter(company_id=locked.company_id, name_key=canonical_key(locked.name))
                 .exclude(pk=locked.pk).first())
        if clash is None:
            raise
        raise DuplicateEstate(clash) from None
    _log(locked.company_id, InventoryEvent.Action.ESTATE_EDITED, actor, estate=locked,
         estate_name=locked.name, changes=changes, note=note)
    return locked


# ---------- single plot ----------

@transaction.atomic
def register_plot(company, actor, *, estate, plot_number):
    """Register one plot in an estate, with no sale. Raises ValueError, DuplicatePlot (the plot that already has
    this number is `.plot`), PlotCapExceeded, PermissionDenied."""
    _check_actor(actor, company)
    locked = _fetch_estate(estate, company, lock=True)
    label = clean_plot_label(plot_number)
    key = canonical_key(label)
    existing = Plot.objects.filter(estate=locked, plot_key=key).select_related("estate").first()
    if existing is not None:
        raise DuplicatePlot(existing)
    count = Plot.objects.filter(estate=locked).count()
    if count + 1 > max_plots_per_estate():
        raise PlotCapExceeded(max_plots_per_estate(), count, 1)
    try:
        with transaction.atomic():
            plot = Plot.objects.create(
                company=company,
                estate=locked,
                plot_number=label,
                inventory_registered=True,
            )
    except IntegrityError:
        existing = Plot.objects.filter(estate=locked, plot_key=key).select_related("estate").first()
        if existing is None:
            raise
        raise DuplicatePlot(existing) from None
    _log(company.pk, InventoryEvent.Action.PLOT_REGISTERED, actor, estate=locked, plot=plot,
         changes={"plot_number": [None, label]})
    return plot


@transaction.atomic
def rename_plot(plot, actor, *, plot_number, reason=""):
    """Change a plot's number. Sales stay attached (they point at the plot, not at its number). A plot that has
    ever had a sale needs a reason. Raises ValueError, DuplicatePlot, PermissionDenied."""
    locked = Plot.objects.select_for_update().get(pk=plot.pk)
    _own(actor, locked.company_id, "Plot")
    new_label = clean_plot_label(plot_number)
    old_label = locked.plot_number
    if new_label == old_label:
        raise ValueError("Nothing to change.")
    clash = (Plot.objects.filter(estate_id=locked.estate_id, plot_key=canonical_key(new_label))
             .exclude(pk=locked.pk).select_related("estate").first())
    if clash is not None:
        raise DuplicatePlot(clash)
    note = _reason(reason) if plot_has_sales(locked) else (reason or "").strip()
    locked.plot_number = new_label
    try:
        with transaction.atomic():
            locked.save(update_fields=["plot_number"])           # the key follows the number
    except IntegrityError:
        clash = (Plot.objects.filter(estate_id=locked.estate_id, plot_key=canonical_key(new_label))
                 .exclude(pk=locked.pk).select_related("estate").first())
        if clash is None:
            raise
        raise DuplicatePlot(clash) from None
    _log(locked.company_id, InventoryEvent.Action.PLOT_RENAMED, actor, estate=locked.estate, plot=locked,
         changes={"plot_number": [old_label, new_label]}, note=note)
    return locked


@transaction.atomic
def remove_plot(plot, actor, *, reason):
    """Remove a plot that was registered by mistake. Only a plot that has NEVER had a sale (cancelled sales
    count) can be removed; anything else raises RemovalBlocked. A reason is required. The history keeps the
    plot's number. Raises ValueError, RemovalBlocked, PermissionDenied."""
    note = _reason(reason)
    locked = Plot.objects.select_for_update().select_related("estate").get(pk=plot.pk)
    _own(actor, locked.company_id, "Plot")
    if plot_has_sales(locked):
        raise RemovalBlocked(
            f"Plot {locked.plot_number!r} has sales history (cancelled sales count), so it cannot be removed.")
    estate, label, company_id = locked.estate, locked.plot_number, locked.company_id
    try:
        locked.delete()
    except ProtectedError:      # defence only: create_sale locks the plot, so a sale cannot slip in after the check
        raise RemovalBlocked(
            f"Plot {label!r} has sales history, so it cannot be removed.") from None
    _log(company_id, InventoryEvent.Action.PLOT_REMOVED, actor, estate=estate, plot_label=label,
         changes={"plot_number": [label, None]}, note=note)


# ---------- bulk registration ----------

OUTCOMES = ("new", "exists", "duplicate_in_request", "invalid")


def expand_entries(entries):
    """The ordered list of labels a bulk request stands for, whitespace-collapsed but not yet validated.

    `entries` is a list; each entry is one of
      {"labels": ["A1", "A2"]}                                  a list of labels, or one string with a label per line
      {"prefix": "A", "start": 1, "end": 40, "pad": 0, "suffix": ""}   a range; pad zero-fills the number
    Several entries in one request cover estates numbered in blocks (A1 to A40, then B1 to B35).
    Raises ValueError. At most BULK_MAX_LABELS labels in all, checked before a range is expanded."""
    if not isinstance(entries, (list, tuple)) or not entries:
        raise ValueError("Give at least one entry (a list of labels or a range).")
    labels, total = [], 0
    for position, entry in enumerate(entries, start=1):
        where = f"Entry {position}"
        if not isinstance(entry, dict):
            raise ValueError(f"{where} must be a list of labels or a range.")
        if "labels" in entry:
            raw = entry["labels"]
            if isinstance(raw, str):
                raw = raw.splitlines()
            if not isinstance(raw, (list, tuple)):
                raise ValueError(f"{where}: labels must be a list, or text with one label per line.")
            produced = [collapse(item) for item in raw if collapse(item)]
        elif "start" in entry and "end" in entry:
            start, end = _whole_number(entry["start"], f"{where}: start"), _whole_number(entry["end"], f"{where}: end")
            pad = _whole_number(entry.get("pad", 0), f"{where}: pad")
            if start < 0 or end < start:
                raise ValueError(f"{where}: the range must run from a number to a number that is not smaller.")
            if not 0 <= pad <= RANGE_MAX_PAD:
                raise ValueError(f"{where}: pad must be between 0 and {RANGE_MAX_PAD}.")
            if end - start + 1 > BULK_MAX_LABELS - total:
                raise ValueError(f"A request can register at most {BULK_MAX_LABELS} labels.")
            prefix, suffix = str(entry.get("prefix") or ""), str(entry.get("suffix") or "")
            if len(prefix) > PLOT_LABEL_MAX or len(suffix) > PLOT_LABEL_MAX:
                raise ValueError(f"{where}: the prefix and suffix are too long.")
            produced = [collapse(f"{prefix}{str(number).zfill(pad)}{suffix}") for number in range(start, end + 1)]
        else:
            raise ValueError(f"{where}: give either labels or a range (start and end).")
        total += len(produced)
        if total > BULK_MAX_LABELS:
            raise ValueError(f"A request can register at most {BULK_MAX_LABELS} labels.")
        labels.extend(produced)
    if not labels:
        raise ValueError("Give at least one label.")
    return labels


def _whole_number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError(f"{name} must be a whole number.")
    try:
        return int(str(value).strip())
    except ValueError:
        raise ValueError(f"{name} must be a whole number.") from None


_LEADING_ZEROS = re.compile(r"(?<![0-9])0+(?=[0-9])")


def _loose_key(key):
    """A canonical key with the leading zeros of every number removed: '05' and '5', 'a-05' and 'a-5'. Used only to
    WARN that two labels may be the same plot written two ways (a spreadsheet's numeric cell is 5, never 05)."""
    return _LEADING_ZEROS.sub("", key)


@dataclass
class Classification:
    items: list = field(default_factory=list)
    existing_count: int = 0
    limit: int = 0

    def count(self, outcome):
        return sum(1 for item in self.items if item["outcome"] == outcome)

    @property
    def summary(self):
        new = self.count("new")
        return {
            "total": len(self.items), "new": new, "exists": self.count("exists"),
            "duplicate_in_request": self.count("duplicate_in_request"), "invalid": self.count("invalid"),
            "warnings": sum(1 for item in self.items if item["warnings"]),
            "existing_plots": self.existing_count, "limit": self.limit,
            "cap_exceeded": self.existing_count + new > self.limit,
        }


def classify_labels(estate, labels):
    """What each label would do in this estate right now. The ONE function both the preview and the commit use,
    so the two can never disagree about what a label means.

    Per label: new; exists (already registered, by canonical key: skipped); duplicate_in_request (the same key as
    an earlier label in this request: skipped); invalid (blank, too long, invisible characters: blocks the commit).
    A `new` label can also carry warnings (looks like another label apart from leading zeros); a warning never
    changes the outcome. Reads only; takes no lock."""
    existing = {key: (pk, number) for pk, number, key
                in Plot.objects.filter(estate=estate).values_list("pk", "plot_number", "plot_key")}
    loose_existing = defaultdict(list)
    for key, (_, number) in existing.items():
        loose_existing[_loose_key(key)].append((key, number))
    result = Classification(existing_count=len(existing), limit=max_plots_per_estate())
    first_seen, loose_new = {}, defaultdict(list)
    for index, raw in enumerate(labels):
        item = {"index": index, "label": raw, "key": "", "outcome": "invalid", "message": "",
                "existing_plot": None, "warnings": []}
        result.items.append(item)
        try:
            label = clean_plot_label(raw)
        except ValueError as exc:
            item["message"] = str(exc)
            continue
        key = item["key"] = canonical_key(label)
        if key in first_seen:
            item["outcome"] = "duplicate_in_request"
            item["message"] = f"Same as item {first_seen[key] + 1} once letter case and spacing are ignored."
            continue
        first_seen[key] = index
        if key in existing:
            item["outcome"] = "exists"
            item["existing_plot"] = existing[key][0]
            item["message"] = "Already registered in this estate."
            continue
        item["outcome"] = "new"
        loose = _loose_key(key)
        for other_key, number in loose_existing.get(loose, []):
            if other_key != key:
                item["warnings"].append(f"Looks like the existing plot {number!r}, apart from leading zeros.")
        for other_label in loose_new.get(loose, []):
            item["warnings"].append(f"Looks like {other_label!r} in this request, apart from leading zeros.")
        loose_new[loose].append(label)
    return result


def _digest(payload):
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def input_fingerprint(company_id, estate_id, labels):
    """SHA-256 of the canonical JSON of the company, the estate and the ordered, whitespace-collapsed,
    case-preserved labels after ranges are expanded. Two requests that expand to the same labels are the same
    input; any edited, added, removed or reordered label changes it."""
    return _digest({"company": company_id, "estate": estate_id, "labels": list(labels)})


def preview_fingerprint(items):
    """SHA-256 of what each label would do: its text, its canonical key and its outcome, in order. It changes if
    any label's outcome changes, even when the totals stay the same (one label becoming 'exists' while another
    becomes 'new'). Warnings and messages are not part of it: they never change what a commit does."""
    return _digest([[item["label"], item["key"], item["outcome"]] for item in items])


def _describe(estate, classification, labels, company_id):
    summary = classification.summary
    return {
        "estate": {"id": estate.pk, "name": estate.name},
        "items": classification.items,
        "summary": summary,
        "can_commit": summary["new"] > 0 and summary["invalid"] == 0 and not summary["cap_exceeded"],
        "input_fingerprint": input_fingerprint(company_id, estate.pk, labels),
        "preview_fingerprint": preview_fingerprint(classification.items),
    }


def preview_bulk(company, actor, *, estate, entries):
    """What registering these labels would do. Writes nothing and takes no lock. Returns the items, a summary,
    `can_commit` and the two fingerprints the commit must send back. Raises ValueError, PermissionDenied."""
    _check_actor(actor, company)
    found = _fetch_estate(estate, company)
    labels = expand_entries(entries)
    return _describe(found, classify_labels(found, labels), labels, company.pk)


@dataclass
class BulkResult:
    created: list
    skipped_existing: list
    skipped_repeated: list


def _insert_plots(plots):
    """The one place the bulk insert happens (a seam: tests use it to put a competing writer in the gap)."""
    return Plot.objects.bulk_create(plots)


@transaction.atomic
def commit_bulk(company, actor, *, estate, entries, input_fingerprint_sent, preview_fingerprint_sent):
    """Register the new labels of a previewed request: all of them or none.

    The commit trusts nothing the preview said. It (1) recomputes the input fingerprint from what was sent and
    refuses if it is not the previewed input, (2) locks the estate against other registrations, (3) classifies the
    labels again, inside the transaction, with the same function the preview used, and refuses if the
    fingerprint of the outcomes is not the one the person reviewed, (4) re-checks the cap, and (5) inserts in key
    order. If a writer that takes no lock (a sale, an import) creates one of the plots in the gap, the database
    refuses the insert and everything is rolled back.

    Labels that already exist are skipped and reported; they are never changed. Invalid labels block the commit.
    Raises ValueError (input not the previewed input, invalid labels, nothing new), StalePreview, PlotCapExceeded,
    RegistrationRace, PermissionDenied."""
    _check_actor(actor, company)
    labels = expand_entries(entries)
    if not input_fingerprint_sent or not preview_fingerprint_sent:
        raise ValueError("Preview the labels first: the commit needs the fingerprints the preview returned.")
    locked = _fetch_estate(estate, company, lock=True)
    if input_fingerprint(company.pk, locked.pk, labels) != input_fingerprint_sent:
        raise ValueError("These are not the labels that were previewed. Preview them again.")
    classification = classify_labels(locked, labels)
    if preview_fingerprint(classification.items) != preview_fingerprint_sent:
        raise StalePreview(_describe(locked, classification, labels, company.pk))
    summary = classification.summary
    if summary["invalid"]:
        raise ValueError(f"{summary['invalid']} label(s) are invalid. Fix them and preview again.")
    if summary["new"] == 0:
        raise ValueError("Nothing to register: every label already exists or is repeated.")
    if summary["cap_exceeded"]:
        raise PlotCapExceeded(summary["limit"], summary["existing_plots"], summary["new"])

    new_items = [item for item in classification.items if item["outcome"] == "new"]
    # Inserted in key order, so two bulk registrations that overlap take their index locks in the same order.
    ordered = sorted(new_items, key=lambda item: (item["key"], item["index"]))
    plots = [
        Plot(
            company=company,
            estate=locked,
            plot_number=item["label"],
            inventory_registered=True,
        )
        for item in ordered
    ]
    try:
        with transaction.atomic():
            created = _insert_plots(plots)
    except IntegrityError:
        raise RegistrationRace() from None
    skipped_existing = [item["label"] for item in classification.items if item["outcome"] == "exists"]
    skipped_repeated = [item["label"] for item in classification.items if item["outcome"] == "duplicate_in_request"]
    _log(company.pk, InventoryEvent.Action.PLOTS_BULK_REGISTERED, actor, estate=locked, changes={
        "count": len(created), "labels": [item["label"] for item in new_items],
        "skipped_existing": skipped_existing, "skipped_repeated": skipped_repeated,
        "input_fingerprint": input_fingerprint_sent, "preview_fingerprint": preview_fingerprint_sent})
    return BulkResult(created=created, skipped_existing=skipped_existing, skipped_repeated=skipped_repeated)
