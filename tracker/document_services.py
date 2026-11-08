"""Document catalogue services (Phase 1C-1). The ONLY way catalogue entries should be changed.

A catalogue entry (DocumentType) is paired with one or more work types (ObligationType rows that point at it
through `catalogue_entry`). Exactly one of them is the entry's CURRENT work type (`is_current=True`, enforced
by the database); the others are earlier work types, kept so that obligations created under an earlier name
keep that name.

Rules implemented here:

- Names are unique per company ignoring case, across catalogue entries AND the company's work types.
- Renaming an entry never rewrites history:
    * the entry's current work type has no obligations -> it is renamed together with the entry;
    * it has obligations -> a replacement work type with the new name becomes current (copying trigger, SLA
      and default department, `auto_generate=False`) and the earlier one is left exactly as it was;
    * an earlier work type already has the new name -> that one becomes current again (no new row).
  `SaleDocument.name_snapshot` is never touched.
- Deactivating an entry only stops it being offered for new document-managed sales. It never changes
  `ObligationType.is_active`, so legacy automatic generation is untouched.
- SLA days and default department are stored on the current work type (an entry has no such fields). For an
  entry that came from an existing automatic type this is that same type, so changing them changes the due
  dates of obligations generated LATER, exactly as editing the type in the old settings always did.
- Every change writes one DocumentTypeEvent (who, what, before/after).

Raises CatalogueError (bad input, `.field` names the input at fault), CatalogueStateError (wrong state) and
django PermissionDenied (another company's record or actor).
"""
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction

from .models import Department, DocumentType, DocumentTypeEvent, ObligationType
from .services import _clean_text

NAME_LIMIT = 120
CATEGORY_LIMIT = 60
DESCRIPTION_LIMIT = 1000
SORT_ORDER_MAX = 1_000_000
SLA_MIN, SLA_MAX = 1, 3650
DEFAULT_SLA_DAYS = ObligationType._meta.get_field("sla_days").default

UNSET = object()        # "not supplied" for fields where None is a real value (the default department)


class CatalogueError(ValueError):
    """The request breaks a rule. `field` names the input at fault so a form can show the message beside it."""

    def __init__(self, message, field=None):
        super().__init__(message)
        self.field = field


class CatalogueStateError(CatalogueError):
    """The entry is in the wrong state for this action (for example: activating one that is already active)."""


# ---------- reads ----------

def current_work_type(entry):
    """The entry's current work type, or None."""
    return ObligationType.objects.filter(catalogue_entry=entry, is_current=True).first()


def work_types(entry):
    """Every work type that has ever belonged to the entry, oldest first."""
    return ObligationType.objects.filter(catalogue_entry=entry).order_by("id")


# ---------- input checks ----------

def _name(value):
    text = _clean_text(value)
    if not text:
        raise CatalogueError("Name is required.", "name")
    if len(text) > NAME_LIMIT:
        raise CatalogueError(f"Name is too long (maximum {NAME_LIMIT} characters).", "name")
    return text


def _category(value):
    text = _clean_text(value)
    if len(text) > CATEGORY_LIMIT:
        raise CatalogueError(f"Category is too long (maximum {CATEGORY_LIMIT} characters).", "category")
    return text


def _description(value):
    text = (value or "").strip()
    if len(text) > DESCRIPTION_LIMIT:
        raise CatalogueError(f"Description is too long (maximum {DESCRIPTION_LIMIT} characters).", "description")
    return text


def _sort_order(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= SORT_ORDER_MAX:
        raise CatalogueError(f"Sort order must be a whole number from 0 to {SORT_ORDER_MAX}.", "sort_order")
    return value


def _sla(value):
    if isinstance(value, bool) or not isinstance(value, int) or not SLA_MIN <= value <= SLA_MAX:
        raise CatalogueError(f"SLA days must be a whole number from {SLA_MIN} to {SLA_MAX}.", "sla_days")
    return value


def _department(company, department):
    if department is None:
        return None
    if not isinstance(department, Department) or department.company_id != company.pk:
        raise CatalogueError("Department belongs to another company.", "default_department")
    return department


def _check_name_free(company, name, entry=None):
    """Case-insensitive, across the company's catalogue entries and work types. Work types that already belong
    to `entry` do not count against it (that is how renaming back works)."""
    entries = DocumentType.objects.filter(company=company, name__iexact=name)
    if entry is not None:
        entries = entries.exclude(pk=entry.pk)
    if entries.exists():
        raise CatalogueError("A document type with this name already exists.", "name")
    types = ObligationType.objects.filter(company=company, name__iexact=name)
    if entry is not None:
        types = types.exclude(catalogue_entry=entry)
    if types.exists():
        raise CatalogueError("A work type with this name already exists in the settings, so the name is taken.", "name")


def _lock(entry, actor):
    locked = DocumentType.objects.select_for_update().get(pk=entry.pk)
    if actor.company_id != locked.company_id:
        raise PermissionDenied("Document type belongs to another company.")
    return locked


def _log(entry, actor, action, changes, note=""):
    return DocumentTypeEvent.objects.create(
        company_id=entry.company_id, document_type=entry, actor=actor, action=action, changes=changes, note=note)


# ---------- writes ----------

@transaction.atomic
def create_document_type(company, actor, *, name, description="", category="", sort_order=0,
                         sla_days=None, default_department=None):
    """Add a document type to the company's catalogue together with its first (current) work type:
    trigger on_sale, active, `auto_generate=False` so it never joins legacy automatic generation."""
    if actor.company_id != company.pk:
        raise PermissionDenied("You belong to another company.")
    name = _name(name)
    description = _description(description)
    category = _category(category)
    sort_order = _sort_order(sort_order)
    sla = DEFAULT_SLA_DAYS if sla_days is None else _sla(sla_days)
    department = _department(company, default_department)
    _check_name_free(company, name)
    try:
        with transaction.atomic():
            entry = DocumentType.objects.create(
                company=company, name=name, description=description, category=category, sort_order=sort_order)
            work = ObligationType.objects.create(
                company=company, name=name, trigger=ObligationType.Trigger.ON_SALE, sla_days=sla,
                default_department=department, is_active=True, auto_generate=False,
                catalogue_entry=entry, is_current=True)
    except IntegrityError:
        raise CatalogueError("A document type with this name already exists.", "name") from None
    _log(entry, actor, DocumentTypeEvent.Action.CREATED, {
        "name": [None, name], "description": [None, description], "category": [None, category],
        "sort_order": [None, sort_order], "sla_days": [None, sla],
        "default_department": [None, department.pk if department else None], "work_type": [None, work.pk],
    })
    return entry


@transaction.atomic
def update_document_type(entry, actor, *, description=None, category=None, sort_order=None,
                         sla_days=None, default_department=UNSET):
    """Change the entry's details. Only what is passed is changed. The name has its own action (rename).
    `sla_days` and `default_department` are stored on the current work type and affect obligations created
    from now on only: existing obligations keep their due dates."""
    entry = _lock(entry, actor)
    diff, entry_fields = {}, []
    if description is not None:
        value = _description(description)
        if value != entry.description:
            diff["description"] = [entry.description, value]
            entry.description = value
            entry_fields.append("description")
    if category is not None:
        value = _category(category)
        if value != entry.category:
            diff["category"] = [entry.category, value]
            entry.category = value
            entry_fields.append("category")
    if sort_order is not None:
        value = _sort_order(sort_order)
        if value != entry.sort_order:
            diff["sort_order"] = [entry.sort_order, value]
            entry.sort_order = value
            entry_fields.append("sort_order")
    work_fields = {}
    if sla_days is not None or default_department is not UNSET:
        work = ObligationType.objects.select_for_update().filter(catalogue_entry=entry, is_current=True).first()
        if work is None:
            raise CatalogueStateError("This document type has no current work type, so its SLA and department "
                                      "cannot be changed.")
        if sla_days is not None:
            value = _sla(sla_days)
            if value != work.sla_days:
                diff["sla_days"] = [work.sla_days, value]
                work_fields["sla_days"] = value
        if default_department is not UNSET:
            department = _department(entry.company, default_department)
            new_id = department.pk if department else None
            if new_id != work.default_department_id:
                diff["default_department"] = [work.default_department_id, new_id]
                work_fields["default_department"] = department
    if not diff:
        raise CatalogueError("Nothing to change.")
    if entry_fields:
        entry.save(update_fields=entry_fields)
    if work_fields:
        ObligationType.objects.filter(pk=work.pk).update(**work_fields)
    _log(entry, actor, DocumentTypeEvent.Action.EDITED, diff)
    return entry


@transaction.atomic
def rename_document_type(entry, actor, new_name):
    """Rename the entry without rewriting history (see the module notes for the three cases)."""
    entry = _lock(entry, actor)
    new_name = _name(new_name)
    if new_name == entry.name:
        raise CatalogueError("The name is unchanged.", "name")
    _check_name_free(entry.company, new_name, entry)
    types = list(ObligationType.objects.select_for_update().filter(catalogue_entry=entry).order_by("id"))
    current = next((t for t in types if t.is_current), None)
    if current is None:
        raise CatalogueStateError("This document type has no current work type, so it cannot be renamed.")
    earlier = next((t for t in types if t.name == new_name and not t.is_current), None)
    old_name = entry.name
    try:
        with transaction.atomic():
            if earlier is not None:
                ObligationType.objects.filter(pk=current.pk).update(is_current=False)
                ObligationType.objects.filter(pk=earlier.pk).update(is_current=True)
                chosen, how = earlier.pk, "An earlier work type with this name was made current again."
            elif not current.obligations.exists():
                ObligationType.objects.filter(pk=current.pk).update(name=new_name)
                chosen, how = current.pk, "The work type had no obligations, so it was renamed with the entry."
            else:
                ObligationType.objects.filter(pk=current.pk).update(is_current=False)
                replacement = ObligationType.objects.create(
                    company=entry.company, name=new_name, trigger=current.trigger, sla_days=current.sla_days,
                    default_department=current.default_department, is_active=True, auto_generate=False,
                    catalogue_entry=entry, is_current=True)
                chosen, how = replacement.pk, "A replacement work type was created; earlier obligations keep the old name."
            entry.name = new_name
            entry.save(update_fields=["name"])
    except IntegrityError:
        raise CatalogueError("That name is already in use.", "name") from None
    _log(entry, actor, DocumentTypeEvent.Action.RENAMED,
         {"name": [old_name, new_name], "work_type": [current.pk, chosen]}, how)
    return entry


@transaction.atomic
def set_document_type_active(entry, actor, active):
    """Activate or deactivate an entry. Deactivating only stops it being offered for new document-managed
    sales: the work types' own `is_active` is never changed, so legacy generation and imports carry on."""
    entry = _lock(entry, actor)
    active = bool(active)
    if entry.is_active == active:
        raise CatalogueStateError("This document type is already " + ("active." if active else "inactive."))
    entry.is_active = active
    entry.save(update_fields=["is_active"])
    _log(entry, actor,
         DocumentTypeEvent.Action.ACTIVATED if active else DocumentTypeEvent.Action.DEACTIVATED,
         {"is_active": [not active, active]})
    return entry
