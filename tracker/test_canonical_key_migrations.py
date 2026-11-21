"""The real migration sequence 0012 -> 0013 -> 0014 for canonical keys, run by Django's MigrationExecutor.

Each test rolls the schema back to 0011 (before any key column existed), creates records the way the previous
release would have, then migrates forward. It checks what a deployment would see: the columns appear, existing
records are keyed, the constraints take effect, a collision stops the sequence safely, and no record is ever
renamed, merged or deleted by the migrations.

Deliberately NOT covered here (left to the function-level tests in test_canonical_keys.py, and to the manual
checks in the Stage 1 report): very large tables, concurrent writers during a migration, and the behaviour
behind a connection pooler. Those are about scale and the production environment, not about the logic.

TransactionTestCase because schema changes cannot run inside the transaction an ordinary TestCase wraps tests in.
Django runs these after all the ordinary tests. tearDown always returns the schema to the latest migration.
"""
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

BEFORE = ("tracker", "0011_obligationtype_is_current")
STUCK = ("tracker", "0012_estate_name_key_plot_plot_key")
AFTER = ("tracker", "0014_canonical_key_constraints")


def migrate_to(target):
    """Migrate to `target` and return the app registry exactly as the schema is at that point."""
    MigrationExecutor(connection).migrate([target])
    return MigrationExecutor(connection).loader.project_state([target]).apps


def applied():
    return {name for app, name in MigrationExecutor(connection).loader.applied_migrations if app == "tracker"}


class CanonicalKeyMigrationSequenceTests(TransactionTestCase):
    def setUp(self):
        self.old = migrate_to(BEFORE)                  # the schema of the release before the keys
        self.Company = self.old.get_model("tracker", "Company")
        self.Estate = self.old.get_model("tracker", "Estate")
        self.Plot = self.old.get_model("tracker", "Plot")

    def tearDown(self):
        with connection.cursor() as cursor:            # whatever state a test left behind, start the next clean
            for table in ("tracker_plot", "tracker_estate", "tracker_company"):
                cursor.execute(f"DELETE FROM {table}")
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    def records(self, apps):
        Estate, Plot = apps.get_model("tracker", "Estate"), apps.get_model("tracker", "Plot")
        return (list(Estate.objects.order_by("pk").values_list("pk", "company_id", "name", "location")),
                list(Plot.objects.order_by("pk").values_list("pk", "company_id", "estate_id", "plot_number")))

    def keys(self, apps):
        Estate, Plot = apps.get_model("tracker", "Estate"), apps.get_model("tracker", "Plot")
        return (list(Estate.objects.order_by("pk").values_list("name_key", flat=True)),
                list(Plot.objects.order_by("pk").values_list("plot_key", flat=True)))

    def awkward_but_distinct_data(self):
        a = self.Company.objects.create(name="A Homes")
        b = self.Company.objects.create(name="B Homes")
        green = self.Estate.objects.create(company=a, name="Green  Valley")
        royal = self.Estate.objects.create(company=a, name="Royal Gardens")
        other = self.Estate.objects.create(company=b, name="green valley")        # same name, another company
        for company, estate, number in ((a, green, "A-5"), (a, green, " B 6 "), (a, green, "05"), (a, green, "5"),
                                        (a, green, "Straße"), (a, royal, "a-5"), (b, other, "A-5")):
            self.Plot.objects.create(company=company, estate=estate, plot_number=number)
        return green

    def test_the_sequence_keys_existing_records_and_then_enforces_the_constraints(self):
        green = self.awkward_but_distinct_data()
        before = self.records(self.old)

        new = migrate_to(AFTER)
        self.assertTrue({"0012_estate_name_key_plot_plot_key", "0013_backfill_canonical_keys",
                         "0014_canonical_key_constraints"} <= applied())

        self.assertEqual(self.records(new), before)        # nothing renamed, merged, deleted or re-assigned
        estate_keys, plot_keys = self.keys(new)
        self.assertEqual(estate_keys, ["green valley", "royal gardens", "green valley"])
        self.assertEqual(plot_keys, ["a-5", "b 6", "05", "5", "strasse", "a-5", "a-5"])

        Plot = new.get_model("tracker", "Plot")
        Estate = new.get_model("tracker", "Estate")
        for number in ("a-5", " A-5 ", "A-5\t"):
            with self.assertRaises(IntegrityError), transaction.atomic():
                Plot.objects.create(company_id=green.company_id, estate_id=green.pk, plot_number=number,
                                    plot_key=number.strip().casefold())
        with self.assertRaises(IntegrityError), transaction.atomic():
            Estate.objects.create(company_id=green.company_id, name="GREEN VALLEY", name_key="green valley")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Plot.objects.create(company_id=green.company_id, estate_id=green.pk, plot_number="X-1", plot_key="")

    def test_a_collision_stops_the_sequence_after_0012_and_changes_no_record(self):
        company = self.Company.objects.create(name="A Homes")
        palm = self.Estate.objects.create(company=company, name="Palm Grove")
        self.Estate.objects.create(company=company, name="palm  grove")
        for number in ("A-1", "A-5", "a-5 ", "B-1"):
            self.Plot.objects.create(company=company, estate=palm, plot_number=number)
        before = self.records(self.old)

        with self.assertRaises(RuntimeError) as caught:
            migrate_to(AFTER)
        message = str(caught.exception)
        for expected in ("Palm Grove", "palm  grove", "'A-5'", "'a-5 '", "Nothing was changed"):
            self.assertIn(expected, message)

        # The sequence stopped cleanly: the columns exist, but no key was written and no constraint added.
        self.assertEqual(applied() & {"0012_estate_name_key_plot_plot_key", "0013_backfill_canonical_keys",
                                      "0014_canonical_key_constraints"}, {"0012_estate_name_key_plot_plot_key"})
        stuck = MigrationExecutor(connection).loader.project_state([STUCK]).apps
        self.assertEqual(self.records(stuck), before)
        estate_keys, plot_keys = self.keys(stuck)
        self.assertEqual(set(estate_keys) | set(plot_keys), {""})

    def test_while_stopped_after_0012_the_previous_release_can_still_create_records(self):
        """The columns have a database default, so a release that does not know about them keeps working if the
        sequence is stopped part-way (for example by a collision to be fixed by hand)."""
        company = self.Company.objects.create(name="A Homes")
        estate = self.Estate.objects.create(company=company, name="Green Valley")
        self.Plot.objects.create(company=company, estate=estate, plot_number="A-1")
        self.Plot.objects.create(company=company, estate=estate, plot_number="a-1 ")      # a collision
        with self.assertRaises(RuntimeError):
            migrate_to(AFTER)

        # Old-release writes: these historical models know nothing about the key columns.
        self.Estate.objects.create(company=company, name="Old Release Estate")
        late = self.Plot.objects.create(company=company, estate=estate, plot_number="OLD-1")

        # The collision is fixed by hand (here: the duplicate is removed), then the sequence is run again.
        self.Plot.objects.filter(plot_number="a-1 ").delete()
        survivors = self.records(self.old)
        new = migrate_to(AFTER)

        self.assertEqual(self.records(new), survivors)
        Plot, Estate = new.get_model("tracker", "Plot"), new.get_model("tracker", "Estate")
        self.assertEqual(Plot.objects.get(pk=late.pk).plot_key, "old-1")                 # keyed by the re-run
        self.assertEqual(Estate.objects.get(name="Old Release Estate").name_key, "old release estate")
        self.assertFalse(Plot.objects.filter(plot_key="").exists())

    def test_reversing_the_sequence_keeps_every_record(self):
        self.awkward_but_distinct_data()
        before = self.records(self.old)
        migrate_to(AFTER)
        reverted = migrate_to(BEFORE)
        self.assertEqual(self.records(reverted), before)
        self.assertFalse(applied() & {"0012_estate_name_key_plot_plot_key", "0013_backfill_canonical_keys",
                                      "0014_canonical_key_constraints"})
