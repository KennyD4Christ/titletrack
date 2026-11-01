from collections import defaultdict

from django.db import migrations, models

# Everything about an obligation type that the data step must leave exactly as it found it.
SNAPSHOT_FIELDS = (
    "id", "company_id", "name", "trigger", "sla_days", "default_department_id", "is_active", "auto_generate",
    "catalogue_entry_id",
)
CHUNK = 500


def _snapshot(ObligationType):
    return list(ObligationType.objects.order_by("id").values_list(*SNAPSHOT_FIELDS))


def mark_current_work_types(apps, schema_editor):
    """Make the work type of each catalogue entry its CURRENT work type.

    After Phase 1B every entry has exactly one work type, so this is unambiguous. If any entry has more than
    one, the migration stops and changes nothing: choosing between them would be a guess. Only `is_current` is
    written; every other column is verified unchanged afterwards. Safe to run twice."""
    ObligationType = apps.get_model("tracker", "ObligationType")
    DocumentType = apps.get_model("tracker", "DocumentType")

    per_entry = defaultdict(list)
    for pk, entry_id in (ObligationType.objects.filter(catalogue_entry__isnull=False)
                         .order_by("id").values_list("pk", "catalogue_entry_id")):
        per_entry[entry_id].append(pk)

    several = {entry_id: ids for entry_id, ids in per_entry.items() if len(ids) > 1}
    if several:
        names = dict(DocumentType.objects.filter(pk__in=list(several)).values_list("pk", "name"))
        lines = [f"catalogue entry {entry_id} ({names.get(entry_id, '?')}): obligation types {ids}"
                 for entry_id, ids in sorted(several.items())]
        raise RuntimeError(
            "Cannot decide which obligation type is current because some catalogue entries have more than one: "
            + "; ".join(lines) + ". Nothing was changed.")

    before = _snapshot(ObligationType)
    chosen = [ids[0] for ids in per_entry.values()]
    for start in range(0, len(chosen), CHUNK):
        ObligationType.objects.filter(pk__in=chosen[start:start + CHUNK]).update(is_current=True)

    if _snapshot(ObligationType) != before:
        raise RuntimeError("An obligation type changed during the backfill. Nothing was changed.")
    current = defaultdict(int)
    for entry_id in ObligationType.objects.filter(is_current=True).values_list("catalogue_entry_id", flat=True):
        current[entry_id] += 1
    if set(current) != set(per_entry) or any(n != 1 for n in current.values()):
        raise RuntimeError("Every catalogue entry must have exactly one current work type. Nothing was changed.")


class Migration(migrations.Migration):

    dependencies = [
        ("tracker", "0010_backfill_document_catalogue"),
    ]

    # All schema changes come first and the data step last, so no ALTER TABLE follows an UPDATE in the same
    # transaction (PostgreSQL can refuse that).
    operations = [
        migrations.AddField(
            model_name="obligationtype",
            name="is_current",
            field=models.BooleanField(db_default=False, default=False),
        ),
        migrations.AddConstraint(
            model_name="obligationtype",
            constraint=models.UniqueConstraint(
                condition=models.Q(("is_current", True)), fields=("catalogue_entry",),
                name="one_current_worktype_per_entry"),
        ),
        migrations.AddConstraint(
            model_name="obligationtype",
            constraint=models.CheckConstraint(
                condition=models.Q(("is_current", False), ("catalogue_entry__isnull", False), _connector="OR"),
                name="current_worktype_has_entry"),
        ),
        migrations.RunPython(mark_current_work_types, migrations.RunPython.noop),
    ]
