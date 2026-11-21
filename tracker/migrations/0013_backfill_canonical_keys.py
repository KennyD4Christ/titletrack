import re
from collections import defaultdict

from django.db import migrations

CHUNK = 500         # rows read per query and written per UPDATE
SHOWN = 25          # how many problems the error message lists before summarising the rest

# FROZEN COPY of the canonical-key rule. Do not import the application's version and do not "keep it in sync".
# A migration must do exactly what it did when it was written, however the application's rules change later:
# if the live rule is ever changed, this migration must keep producing the keys that migrations 0013 and 0014
# were built for. (The application and this copy are compared by a test, which will say so if they ever
# differ, and a human then decides what that means.) The rule: collapse every run of whitespace to one space,
# strip leading and trailing whitespace, apply Unicode case folding. Punctuation, leading zeros and other
# formatting are left alone.
_WHITESPACE = re.compile(r"\s+")


def frozen_canonical_key(value):
    return _WHITESPACE.sub(" ", str(value or "")).strip().casefold()


def _batches(Model, *fields):
    """Yield lists of (pk, *fields), at most CHUNK rows each, in primary-key order.

    Keyset pagination (pk greater than the last one seen), so every query is bounded, the order is the same on
    every run, and rows written between batches cannot cause a row to be skipped or read twice. It uses plain
    LIMIT queries, not server-side cursors, which a connection pooler can break.
    The base manager is used on purpose: it is the plain manager, so this code behaves the same on the historical
    models a migration is given and on the live models a test hands it."""
    last = None
    while True:
        rows = Model._base_manager.order_by("pk")
        if last is not None:
            rows = rows.filter(pk__gt=last)
        batch = list(rows.values_list("pk", *fields)[:CHUNK])
        if not batch:
            return
        yield batch
        last = batch[-1][0]


def _rows(Model, *fields):
    for batch in _batches(Model, *fields):
        yield from batch


def find_key_problems(estates, plots):
    """Everything that would stop the canonical-key constraints from being added, as readable lines.

    `estates` is an iterable of (id, company_id, name); `plots` of (id, estate_id, plot_number). Pure: it reads
    nothing and writes nothing. Two estates of one company, or two plots of one estate, collide when their names
    differ only by letter case or whitespace. A name that is empty once whitespace is removed has no usable key.
    It holds one short key per record (not the records themselves) while it looks for repeats."""
    problems = []
    estate_names = {}
    seen = defaultdict(list)
    for pk, company_id, name in estates:
        estate_names[pk] = name
        key = frozen_canonical_key(name)
        if not key:
            problems.append(f"estate {pk} has a blank name ({name!r})")
        else:
            seen[(company_id, key)].append((pk, name))
    for (company_id, _), found in sorted(seen.items()):
        if len(found) > 1:
            problems.append(f"company {company_id} has estates that differ only by case or spacing: "
                            + ", ".join(f"{pk} {name!r}" for pk, name in sorted(found)))

    seen = defaultdict(list)
    for pk, estate_id, number in plots:
        key = frozen_canonical_key(number)
        if not key:
            problems.append(f"plot {pk} has a blank plot number ({number!r})")
        else:
            seen[(estate_id, key)].append((pk, number))
    for (estate_id, _), found in sorted(seen.items()):
        if len(found) > 1:
            problems.append(f"estate {estate_id} ({estate_names.get(estate_id, '?')!r}) has plots that differ only "
                            "by case or spacing: " + ", ".join(f"{pk} {number!r}" for pk, number in sorted(found)))
    return problems


def _write_keys(Model, source_field, key_field):
    """Set `key_field` on every row whose stored key is not the canonical key of `source_field`, one bounded batch
    at a time: read CHUNK rows, build objects only for the stale ones among them, write those, move on. Memory
    never holds more than one batch, whatever the table size. Only the key column is written. Rows that already
    have the right key are skipped, so a second run writes nothing. Returns how many rows were written."""
    written = 0
    for batch in _batches(Model, source_field, key_field):
        stale = [Model(pk=pk, **{key_field: frozen_canonical_key(value)})
                 for pk, value, stored in batch if stored != frozen_canonical_key(value)]
        if stale:
            Model._base_manager.bulk_update(stale, [key_field])
            written += len(stale)
    return written


def _count_wrong_keys(Model, source_field, key_field):
    return sum(1 for _, value, stored in _rows(Model, source_field, key_field)
               if stored != frozen_canonical_key(value))


def backfill_canonical_keys(apps, schema_editor):
    """Give every estate its name_key and every plot its plot_key.

    Preflight first: if any two estates of a company (or two plots of an estate) would share a key, or any name
    has no usable key, the migration stops BEFORE writing anything and says exactly which records are involved.
    It never renames, merges or deletes a record: choosing between colliding records is a decision for a person.
    Only the two key columns are written, in bounded batches. Safe to run twice: rows that already have the right
    key are skipped. The keys are verified afterwards, and any difference fails the migration, which rolls
    everything back (the migration runs in one transaction)."""
    Estate = apps.get_model("tracker", "Estate")
    Plot = apps.get_model("tracker", "Plot")

    problems = find_key_problems(
        _rows(Estate, "company_id", "name"),
        _rows(Plot, "estate_id", "plot_number"))
    if problems:
        shown = problems[:SHOWN]
        more = f" (and {len(problems) - SHOWN} more)" if len(problems) > SHOWN else ""
        raise RuntimeError(
            "Estate and plot keys cannot be added because " + str(len(problems)) + " problem(s) were found: "
            + "; ".join(shown) + more + ". Rename one of each colliding pair (or fix the blank value), then run "
            "migrate again. Nothing was changed.")

    _write_keys(Estate, "name", "name_key")
    _write_keys(Plot, "plot_number", "plot_key")

    wrong = _count_wrong_keys(Estate, "name", "name_key") + _count_wrong_keys(Plot, "plot_number", "plot_key")
    if wrong:
        raise RuntimeError(f"{wrong} estate or plot key(s) are wrong after the backfill. Nothing was changed.")


class Migration(migrations.Migration):
    """Step 2 of 3 for canonical keys: the data step, on its own (see 0012)."""

    dependencies = [
        ("tracker", "0012_estate_name_key_plot_plot_key"),
    ]

    operations = [
        migrations.RunPython(backfill_canonical_keys, migrations.RunPython.noop),
    ]
