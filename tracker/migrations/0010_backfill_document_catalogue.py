from django.db import migrations

NOTE = "Created from an existing obligation type when the document catalogue was introduced"

# Everything about an obligation type that the backfill must leave exactly as it found it.
SNAPSHOT_FIELDS = (
    "id", "company_id", "name", "trigger", "sla_days", "default_department_id", "is_active", "auto_generate",
)


def _snapshot(ObligationType):
    return list(ObligationType.objects.order_by("id").values_list(*SNAPSHOT_FIELDS))


def backfill_document_catalogue(apps, schema_editor):
    """Give every obligation type that has no catalogue entry one DocumentType (same company, name and active
    flag), link it through ObligationType.catalogue_entry and record one 'created' event for it.

    Nothing else changes: no obligation type field, sale, obligation or SaleDocument is written (the link is a
    column-only update). Safe to run twice: types that already have an entry are skipped. Aborts, changing
    nothing, if two names in one company differ only by letter case (the catalogue treats them as the same
    name). Fails, and so rolls everything back, if the verification at the end finds any difference."""
    Company = apps.get_model("tracker", "Company")
    ObligationType = apps.get_model("tracker", "ObligationType")
    DocumentType = apps.get_model("tracker", "DocumentType")
    DocumentTypeEvent = apps.get_model("tracker", "DocumentTypeEvent")
    Sale = apps.get_model("tracker", "Sale")
    Obligation = apps.get_model("tracker", "Obligation")
    SaleDocument = apps.get_model("tracker", "SaleDocument")

    pending = list(ObligationType.objects.filter(catalogue_entry__isnull=True).order_by("company_id", "id"))
    if not pending:
        return

    # 1. Preflight: refuse before writing anything if two names would collide in the catalogue.
    names = {}
    for company_id, name in DocumentType.objects.values_list("company_id", "name"):
        names.setdefault((company_id, name.lower()), []).append(name)
    for t in pending:
        names.setdefault((t.company_id, t.name.lower()), []).append(t.name)
    clashes = {key: found for key, found in names.items() if len(found) > 1}
    if clashes:
        company_names = dict(Company.objects.filter(pk__in={c for c, _ in clashes}).values_list("pk", "name"))
        lines = [
            f"company {company_id} ({company_names.get(company_id, '?')}): " + ", ".join(repr(n) for n in found)
            for (company_id, _), found in sorted(clashes.items())
        ]
        raise RuntimeError(
            "The document catalogue cannot be created because some obligation types have names that differ "
            "only by letter case: " + "; ".join(lines) + ". Rename one of each pair in the Django admin, then "
            "run the migration again. Nothing was changed.")

    before = _snapshot(ObligationType)
    untouched = (Sale.objects.count(), Obligation.objects.count(), SaleDocument.objects.count())

    # 2. Create and link.
    for t in pending:
        entry = DocumentType.objects.create(company_id=t.company_id, name=t.name, is_active=t.is_active)
        ObligationType.objects.filter(pk=t.pk).update(catalogue_entry=entry)
        DocumentTypeEvent.objects.create(
            company_id=t.company_id, document_type=entry, actor=None, action="created",
            changes={"name": [None, t.name], "is_active": [None, t.is_active]}, note=NOTE)

    # 3. Verify. Any difference raises, which rolls the whole migration back.
    if _snapshot(ObligationType) != before:
        raise RuntimeError("An obligation type changed during the backfill. Nothing was changed.")
    if (Sale.objects.count(), Obligation.objects.count(), SaleDocument.objects.count()) != untouched:
        raise RuntimeError("Sales, obligations or sale documents changed during the backfill. Nothing was changed.")
    if ObligationType.objects.filter(catalogue_entry__isnull=True).exists():
        raise RuntimeError("Some obligation types were left without a catalogue entry. Nothing was changed.")


class Migration(migrations.Migration):

    dependencies = [
        ("tracker", "0009_obligationtype_auto_generate_sale_documents_managed_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill_document_catalogue, migrations.RunPython.noop),
    ]
