"""Canonical keys for estate names and plot numbers (inventory Phase 1, stage 1).

Covers: the key function and its agreement with the two older normalisers; the keys being kept in step by every
write path (save, create, get_or_create, bulk_create, bulk_update, update, related managers); the database
refusing case and spacing duplicates whichever path wrote them; the 0013 data step; and the migrations
matching the models. A key is derived: the tests also prove it cannot be written independently of its source
by save(update_fields=...), update(), bulk_update() or bulk_create(update_conflicts=...).
"""
import ast
import importlib
import inspect
import sys
from io import StringIO
from unittest import mock

from django.apps import apps
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.db.models import F, Value
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext

from importer.validation import norm
from tracker import services
from tracker.canonical import MAX_CASEFOLD_GROWTH, canonical_key
from tracker.models import Company, Estate, Plot

backfill = importlib.import_module("tracker.migrations.0013_backfill_canonical_keys")

CORPUS = [
    "A-5", "a-5", "  A-5  ", "A  -  5", "A\t5", "A\n5", "A\u00a05", "A\u20035", "\u3000A5\u3000",
    "Plot 12", "plot   12", "PLOT 12", "12", "012", "5", "Straße", "STRASSE", "İstanbul", "ΐ", "ǅ", "ﬃ",
    "Palm Grove", "palm  grove", "PALM\tGROVE", "", "   ", None, 12, 12.0,
]


class CanonicalKeyFunctionTests(SimpleTestCase):
    def test_it_collapses_whitespace_strips_and_case_folds(self):
        self.assertEqual(canonical_key("  Palm \t Grove\n"), "palm grove")
        self.assertEqual(canonical_key("Straße"), "strasse")
        self.assertEqual(canonical_key("A\u00a0 5"), "a 5")                 # non-breaking space counts as whitespace
        self.assertEqual(canonical_key(None), "")
        self.assertEqual(canonical_key("   "), "")

    def test_it_agrees_with_the_older_normalisers(self):
        """tracker.services._key (used by add_sale) and importer.validation.norm (used by the importer) must give
        the same answer as the stored key. If either is ever changed, this fails."""
        for value in CORPUS:
            self.assertEqual(canonical_key(value), services._key(value), repr(value))
            self.assertEqual(canonical_key(value), norm(value), repr(value))

    def test_the_frozen_copy_in_migration_0013_agrees_with_the_application_rule(self):
        """0013 keeps its own copy of the rule so that changing the application's rule can never silently change
        what an existing migration does. This test is what notices if the two ever differ."""
        for value in CORPUS:
            self.assertEqual(backfill.frozen_canonical_key(value), canonical_key(value), repr(value))

    def test_the_frozen_copy_agrees_for_every_unicode_character(self):
        different = [code for code in range(sys.maxunicode + 1)
                     if backfill.frozen_canonical_key(chr(code)) != canonical_key(chr(code))
                     or backfill.frozen_canonical_key(f"a{chr(code)}b ") != canonical_key(f"a{chr(code)}b ")]
        self.assertEqual(different, [])

    def test_migration_0013_does_not_import_application_code(self):
        tree = ast.parse(inspect.getsource(backfill))
        imported = [node.module if isinstance(node, ast.ImportFrom) else alias.name
                    for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
                    for alias in (node.names if isinstance(node, ast.Import) else [None])]
        self.assertEqual([name for name in imported if name and name.split(".")[0] == "tracker"], [])

    def test_leading_zeros_and_punctuation_are_not_normalised(self):
        keys = {canonical_key(v) for v in ("05", "5", "A-5", "A5")}
        self.assertEqual(len(keys), 4)

    def test_case_folding_never_grows_text_by_more_than_the_factor_the_columns_allow(self):
        worst = max(len(chr(code).casefold()) for code in range(sys.maxunicode + 1))
        self.assertLessEqual(worst, MAX_CASEFOLD_GROWTH)
        for model, source, key in ((Estate, "name", "name_key"), (Plot, "plot_number", "plot_key")):
            self.assertGreaterEqual(model._meta.get_field(key).max_length,
                                    model._meta.get_field(source).max_length * worst)
        self.assertEqual(len(canonical_key("ΐ" * 50)), 150)                  # the worst case fits the plot key column


class KeyTestCase(TestCase):
    def setUp(self):
        self.a = Company.objects.create(name="A Homes")
        self.b = Company.objects.create(name="B Homes")
        self.estate = Estate.objects.create(company=self.a, name="Green Valley")

    def plot(self, number, estate=None):
        return Plot.objects.create(company=self.a, estate=estate or self.estate, plot_number=number)

    def refused(self, make):
        with self.assertRaises(IntegrityError), transaction.atomic():
            make()


class KeyMaintenanceTests(KeyTestCase):
    def test_create_sets_the_key(self):
        self.assertEqual(self.estate.name_key, "green valley")
        self.assertEqual(Estate.objects.get(pk=self.estate.pk).name_key, "green valley")
        plot = self.plot("  A  -  5 ")
        self.assertEqual(plot.plot_key, "a - 5")
        self.assertEqual(Plot.objects.get(pk=plot.pk).plot_key, "a - 5")

    def test_a_key_supplied_by_the_caller_is_ignored(self):
        plot = Plot.objects.create(company=self.a, estate=self.estate, plot_number="B-1", plot_key="wrong")
        self.assertEqual(plot.plot_key, "b-1")

    def test_save_after_a_rename_updates_the_key(self):
        plot = self.plot("A-5")
        plot.plot_number = "A-6"
        plot.save()
        self.assertEqual(Plot.objects.get(pk=plot.pk).plot_key, "a-6")
        self.estate.name = "Royal Gardens"
        self.estate.save()
        self.assertEqual(Estate.objects.get(pk=self.estate.pk).name_key, "royal gardens")

    def test_save_with_update_fields_keeps_the_key_with_the_source(self):
        plot = self.plot("A-5")
        plot.plot_number = "A-7"
        plot.save(update_fields=["plot_number"])
        self.assertEqual(Plot.objects.get(pk=plot.pk).plot_key, "a-7")

    def test_save_with_update_fields_that_exclude_the_source_leaves_both_alone(self):
        plot = self.plot("A-5")
        plot.plot_number = "A-8"
        plot.import_batch = None
        plot.save(update_fields=["import_batch"])
        fresh = Plot.objects.get(pk=plot.pk)
        self.assertEqual((fresh.plot_number, fresh.plot_key), ("A-5", "a-5"))

    def test_get_or_create_and_related_managers_set_the_key(self):
        estate, created = Estate.objects.get_or_create(company=self.a, name="Sunrise Court")
        self.assertTrue(created)
        self.assertEqual(estate.name_key, "sunrise court")
        plot = estate.plots.create(company=self.a, plot_number="C-1")
        self.assertEqual(plot.plot_key, "c-1")

    def test_bulk_create_sets_the_keys(self):
        Plot.objects.bulk_create([Plot(company=self.a, estate=self.estate, plot_number=n) for n in ("D-1", " d  2 ")])
        self.assertEqual(sorted(Plot.objects.filter(estate=self.estate).values_list("plot_key", flat=True)),
                         ["d 2", "d-1"])
        Estate.objects.bulk_create([Estate(company=self.a, name="Lakeside  View")])
        self.assertTrue(Estate.objects.filter(name_key="lakeside view").exists())

    def test_bulk_update_recomputes_the_key_when_the_source_is_updated(self):
        plot = self.plot("E-1")
        plot.plot_number = "E-2"
        Plot.objects.bulk_update([plot], ["plot_number"])
        self.assertEqual(Plot.objects.get(pk=plot.pk).plot_key, "e-2")

    def test_queryset_update_of_the_source_sets_the_key(self):
        plot = self.plot("F-1")
        Plot.objects.filter(pk=plot.pk).update(plot_number="F  2")
        fresh = Plot.objects.get(pk=plot.pk)
        self.assertEqual((fresh.plot_number, fresh.plot_key), ("F  2", "f 2"))
        Estate.objects.filter(pk=self.estate.pk).update(name="Green  Valley II")
        self.assertEqual(Estate.objects.get(pk=self.estate.pk).name_key, "green valley ii")

    def test_queryset_update_with_an_expression_is_refused(self):
        plot = self.plot("G-1")
        with self.assertRaises(TypeError):
            Plot.objects.filter(pk=plot.pk).update(plot_number=Value("G-2"))
        self.assertEqual(Plot.objects.get(pk=plot.pk).plot_number, "G-1")

    def test_updating_other_columns_does_not_touch_the_key(self):
        plot = self.plot("H-1")
        Plot.objects.filter(pk=plot.pk).update(import_batch=None)
        self.assertEqual(Plot.objects.get(pk=plot.pk).plot_key, "h-1")


class DerivedKeyIsNeverWrittenAloneTests(KeyTestCase):
    """The key is derived from its source. No write path may store one without the other."""

    def setUp(self):
        super().setUp()
        self.plot_ = self.plot("A-5")

    def stored(self):
        plot = Plot.objects.get(pk=self.plot_.pk)
        estate = Estate.objects.get(pk=self.estate.pk)
        return plot.plot_number, plot.plot_key, estate.name, estate.name_key

    ORIGINAL = ("A-5", "a-5", "Green Valley", "green valley")

    # ---- save(update_fields=...) ----

    def test_save_of_the_key_alone_is_refused_and_stores_nothing(self):
        self.plot_.plot_number = "A-8"
        with self.assertRaises(ValueError):
            self.plot_.save(update_fields=["plot_key"])
        self.estate.name = "Elsewhere"
        with self.assertRaises(ValueError):
            self.estate.save(update_fields=["name_key"])
        self.assertEqual(self.stored(), self.ORIGINAL)

    def test_save_of_the_key_with_other_columns_but_not_its_source_is_refused(self):
        with self.assertRaises(ValueError):
            self.plot_.save(update_fields=["import_batch", "plot_key"])
        self.assertEqual(self.stored(), self.ORIGINAL)

    def test_save_of_the_key_and_its_source_together_is_allowed_and_the_key_follows_the_source(self):
        self.plot_.plot_number = "A-8"
        self.plot_.plot_key = "tampered"
        self.plot_.save(update_fields=["plot_key", "plot_number"])
        self.assertEqual(Plot.objects.get(pk=self.plot_.pk).plot_key, "a-8")

    def test_update_fields_may_be_any_iterable(self):
        self.plot_.plot_number = "A-9"
        self.plot_.save(update_fields=("plot_number",))
        self.assertEqual(Plot.objects.get(pk=self.plot_.pk).plot_key, "a-9")
        self.plot_.plot_number = "A-10"
        self.plot_.save(update_fields=iter(["plot_number"]))
        self.assertEqual(Plot.objects.get(pk=self.plot_.pk).plot_key, "a-10")
        with self.assertRaises(ValueError):
            self.plot_.save(update_fields=iter(["plot_key"]))

    def test_positional_save_arguments_are_refused_so_update_fields_cannot_hide_in_them(self):
        self.plot_.plot_number = "A-8"
        with self.assertRaises(TypeError):
            self.plot_.save(False, False, None, ["plot_key"])
        with self.assertRaises(TypeError):
            self.estate.save(False)
        self.assertEqual(self.stored(), self.ORIGINAL)

    def test_a_partial_save_without_the_source_leaves_the_instances_key_alone_too(self):
        self.plot_.plot_number = "Z-1"
        self.plot_.save(update_fields=["import_batch"])
        self.assertEqual(self.plot_.plot_key, "a-5")                 # not recomputed in memory either
        self.assertEqual(self.stored(), self.ORIGINAL)

    def test_update_or_create_cannot_write_a_key_on_its_own(self):
        with self.assertRaises(ValueError):
            Plot.objects.update_or_create(estate=self.estate, plot_number="A-5", company=self.a,
                                          defaults={"plot_key": "tampered"})
        self.assertEqual(self.stored(), self.ORIGINAL)

    # ---- QuerySet.update() ----

    def test_update_of_a_key_alone_is_refused(self):
        with self.assertRaises(ValueError):
            Plot.objects.filter(pk=self.plot_.pk).update(plot_key="incorrect")
        with self.assertRaises(ValueError):
            Estate.objects.filter(pk=self.estate.pk).update(name_key="incorrect")
        with self.assertRaises(ValueError):
            Plot.objects.filter(pk=self.plot_.pk).update(plot_key=F("plot_number"))
        self.assertEqual(self.stored(), self.ORIGINAL)

    def test_update_of_a_source_expression_with_a_supplied_key_is_refused(self):
        with self.assertRaises(ValueError):
            Plot.objects.filter(pk=self.plot_.pk).update(plot_number=Value("A-8"), plot_key=Value("a-8"))
        with self.assertRaises(ValueError):
            Estate.objects.filter(pk=self.estate.pk).update(name=Value("X"), name_key=Value("x"))
        self.assertEqual(self.stored(), self.ORIGINAL)

    def test_update_of_a_source_expression_alone_is_refused(self):
        with self.assertRaises(TypeError):
            Plot.objects.filter(pk=self.plot_.pk).update(plot_number=Value("A-8"))
        with self.assertRaises(TypeError):
            Plot.objects.filter(pk=self.plot_.pk).update(plot_number=F("plot_key"))
        self.assertEqual(self.stored(), self.ORIGINAL)

    def test_update_of_a_plain_source_with_a_supplied_key_is_refused_not_silently_corrected(self):
        with self.assertRaises(ValueError):
            Plot.objects.filter(pk=self.plot_.pk).update(plot_number="A-8", plot_key="wrong")
        self.assertEqual(self.stored(), self.ORIGINAL)

    def test_update_of_a_plain_source_still_computes_the_key(self):
        Plot.objects.filter(pk=self.plot_.pk).update(plot_number=" a  8 ")
        Estate.objects.filter(pk=self.estate.pk).update(name="  Lake  VIEW")
        self.assertEqual(self.stored(), (" a  8 ", "a 8", "  Lake  VIEW", "lake view"))

    # ---- QuerySet.bulk_update() ----

    def test_bulk_update_of_a_key_alone_is_refused(self):
        self.plot_.plot_key = "incorrect"
        with self.assertRaises(ValueError):
            Plot.objects.bulk_update([self.plot_], ["plot_key"])
        self.estate.name_key = "incorrect"
        with self.assertRaises(ValueError):
            Estate.objects.bulk_update([self.estate], ["name_key"])
        with self.assertRaises(ValueError):
            Plot.objects.bulk_update([self.plot_], ["import_batch", "plot_key"])
        self.assertEqual(self.stored(), self.ORIGINAL)

    def test_bulk_update_of_a_source_updates_its_key_for_every_object(self):
        second = self.plot("B-1")
        self.plot_.plot_number, second.plot_number = "  c 1", "D\t2"
        Plot.objects.bulk_update([self.plot_, second], ["plot_number"])
        self.assertEqual(sorted(Plot.objects.filter(estate=self.estate).values_list("plot_number", "plot_key")),
                         [("  c 1", "c 1"), ("D\t2", "d 2")])

    def test_bulk_update_overrides_a_key_listed_next_to_its_source(self):
        self.plot_.plot_number, self.plot_.plot_key = "E-1", "tampered"
        Plot.objects.bulk_update([self.plot_], ["plot_number", "plot_key"])
        self.assertEqual(Plot.objects.get(pk=self.plot_.pk).plot_key, "e-1")

    def test_bulk_update_of_an_expression_source_is_refused(self):
        self.plot_.plot_number = F("plot_key")
        with self.assertRaises(TypeError):
            Plot.objects.bulk_update([self.plot_], ["plot_number"])
        self.assertEqual(self.stored(), self.ORIGINAL)

    def test_bulk_update_of_other_columns_works_and_leaves_keys_alone(self):
        self.plot_.import_batch = None
        self.assertEqual(Plot.objects.bulk_update([self.plot_], ["import_batch"]), 1)
        self.assertEqual(self.stored(), self.ORIGINAL)

    def test_the_allowance_for_bulk_updates_does_not_leak_afterwards(self):
        """update() accepts both columns as expressions only while our bulk_update is running. It must be closed
        again after a normal run and after one that failed."""
        self.plot_.plot_number = "F-1"
        Plot.objects.bulk_update([self.plot_], ["plot_number"])
        with self.assertRaises(ValueError):
            Plot.objects.filter(pk=self.plot_.pk).update(plot_number=Value("X"), plot_key=Value("x"))

        taken = self.plot("G-1")
        self.plot_.plot_number = "g-1"                                  # collides with `taken`: the database refuses
        with self.assertRaises(IntegrityError), transaction.atomic():
            Plot.objects.bulk_update([self.plot_], ["plot_number"])
        with self.assertRaises(ValueError):
            Plot.objects.filter(pk=taken.pk).update(plot_number=Value("X"), plot_key=Value("x"))

    # ---- bulk_create(update_conflicts=True) ----

    def test_bulk_create_cannot_rewrite_a_key_on_conflict_without_its_source(self):
        with self.assertRaises(ValueError):
            Plot.objects.bulk_create(
                [Plot(company=self.a, estate=self.estate, plot_number="A-5")], update_conflicts=True,
                unique_fields=["estate", "plot_number"], update_fields=["plot_key"])
        self.assertEqual(self.stored(), self.ORIGINAL)

    def test_bulk_create_with_update_conflicts_carries_the_key_with_the_source(self):
        Plot.objects.bulk_create(
            [Plot(company=self.a, estate=self.estate, plot_number="A-5")], update_conflicts=True,
            unique_fields=["estate", "plot_number"], update_fields=["plot_number"])
        self.assertEqual(self.stored(), self.ORIGINAL)
        self.assertEqual(Plot.objects.filter(estate=self.estate).count(), 1)

    # ---- the base manager: the one deliberate way to write a key directly ----

    def test_the_base_manager_is_the_deliberate_maintenance_path(self):
        Plot._base_manager.filter(pk=self.plot_.pk).update(plot_key="maintenance")
        self.assertEqual(Plot.objects.get(pk=self.plot_.pk).plot_key, "maintenance")


class DatabaseUniquenessTests(KeyTestCase):
    def test_plots_differing_only_by_case_or_spacing_cannot_coexist_in_an_estate(self):
        self.plot("A-5")
        for variant in ("a-5", " A-5", "A-5 ", "A-5\t"):
            self.refused(lambda v=variant: self.plot(v))
        self.refused(lambda: Plot.objects.bulk_create(
            [Plot(company=self.a, estate=self.estate, plot_number="a-5")]))
        self.assertEqual(Plot.objects.filter(estate=self.estate).count(), 1)

    def test_a_rename_into_an_existing_key_is_refused(self):
        self.plot("A-5")
        other = self.plot("A-6")
        other.plot_number = "a-5"
        self.refused(other.save)
        self.refused(lambda: Plot.objects.filter(pk=other.pk).update(plot_number=" A-5"))

    def test_the_same_plot_number_is_fine_in_another_estate(self):
        second = Estate.objects.create(company=self.a, name="Royal Gardens")
        self.plot("A-5")
        self.plot("a-5", estate=second)
        self.assertEqual(Plot.objects.filter(plot_key="a-5").count(), 2)

    def test_estates_differing_only_by_case_or_spacing_cannot_coexist_in_a_company(self):
        for variant in ("green valley", "GREEN   VALLEY", " Green Valley "):
            self.refused(lambda v=variant: Estate.objects.create(company=self.a, name=v))
        self.refused(lambda: Estate.objects.bulk_create([Estate(company=self.a, name="green  valley")]))
        self.assertEqual(Estate.objects.filter(company=self.a).count(), 1)

    def test_the_same_estate_name_is_fine_in_another_company(self):
        Estate.objects.create(company=self.b, name="green valley")
        self.assertEqual(Estate.objects.filter(name_key="green valley").count(), 2)

    def test_a_blank_key_is_refused_by_the_database(self):
        plot = self.plot("A-5")
        with connection.cursor() as cursor, self.assertRaises(IntegrityError), transaction.atomic():
            cursor.execute("UPDATE tracker_plot SET plot_key = '' WHERE id = %s", [plot.pk])
        with connection.cursor() as cursor, self.assertRaises(IntegrityError), transaction.atomic():
            cursor.execute("UPDATE tracker_estate SET name_key = '' WHERE id = %s", [self.estate.pk])
        self.refused(lambda: self.plot("   "))                                  # whitespace only: no usable key
        self.refused(lambda: Estate.objects.create(company=self.a, name=" \t "))

    def test_the_older_exact_match_constraints_are_still_in_force(self):
        names = {c.name for c in Plot._meta.constraints} | {c.name for c in Estate._meta.constraints}
        self.assertTrue({"uniq_plot_per_estate", "uniq_estate_per_company"} <= names)


class ExistingWritePathsTests(KeyTestCase):
    """The application's own creators keep working, and a case-variant race now ends in the existing handler."""

    def test_get_or_create_plot_reuses_a_case_variant(self):
        existing = self.plot("A-5")
        self.assertEqual(services._get_or_create_plot(self.a, self.estate, "a-5"), existing)
        self.assertEqual(Plot.objects.filter(estate=self.estate).count(), 1)

    def test_a_race_for_a_case_variant_returns_the_winner_instead_of_failing(self):
        """The scan sees nothing (the other writer has not committed yet), the insert then collides on the key,
        and the existing IntegrityError handler re-scans and returns the plot that won."""
        winner = self.plot("A-5")
        real_filter = Plot.objects.filter
        scans = []

        def blind_first_scan(*args, **kwargs):
            scans.append(1)
            return [] if len(scans) == 1 else real_filter(*args, **kwargs)

        with mock.patch.object(Plot.objects, "filter", side_effect=blind_first_scan):
            found = services._get_or_create_plot(self.a, self.estate, "a-5")
        self.assertEqual(found, winner)
        self.assertEqual(len(scans), 2)

    def test_a_race_for_an_estate_name_variant_returns_the_winner(self):
        winner = Estate.objects.create(company=self.a, name="Palm Grove")
        real_filter = Estate.objects.filter
        scans = []

        def blind_first_scan(*args, **kwargs):
            scans.append(1)
            return [] if len(scans) == 1 else real_filter(*args, **kwargs)

        with mock.patch.object(Estate.objects, "filter", side_effect=blind_first_scan):
            found = services._get_or_create_estate(self.a, "palm  grove")
        self.assertEqual(found, winner)

    def test_add_sale_onto_a_case_variant_reuses_the_plot(self):
        from datetime import date
        from tracker.models import Buyer, User
        user = User.objects.create_user("mgr", password="x", company=self.a, role="manager")
        existing = self.plot("A-5")
        sale = services.add_sale(self.a, user, plot_number="a-5", price="1000", sale_date=date.today(),
                                 estate=self.estate, buyer_name="Ada Obi")
        self.assertEqual(sale.plot, existing)
        self.assertEqual(Plot.objects.filter(estate=self.estate).count(), 1)
        self.assertEqual(Buyer.objects.filter(company=self.a).count(), 1)


class BackfillTests(KeyTestCase):
    """The real 0013 data step against live models, in the state 0012 leaves behind: keys present but not trusted."""

    def run_step(self):
        backfill.backfill_canonical_keys(apps, None)

    def scramble_keys(self):
        """Put every row into a state the constraints still allow but the backfill must correct."""
        # Model._base_manager is the deliberate way to write a key directly (migrations and tests only):
        # the ordinary `objects` manager refuses it.
        for pk in Estate.objects.values_list("pk", flat=True):
            Estate._base_manager.filter(pk=pk).update(name_key=f"stale-{pk}")
        for pk in Plot.objects.values_list("pk", flat=True):
            Plot._base_manager.filter(pk=pk).update(plot_key=f"stale-{pk}")

    def snapshot(self):
        return (list(Estate.objects.order_by("pk").values_list("pk", "company_id", "name", "location", "name_key")),
                list(Plot.objects.order_by("pk").values_list("pk", "company_id", "estate_id", "plot_number", "plot_key")))

    def test_it_fills_every_key_and_changes_nothing_else(self):
        self.plot("A-5")
        self.plot(" b  6")
        Estate.objects.create(company=self.b, name="Royal Gardens")
        before = self.snapshot()
        self.scramble_keys()
        self.run_step()
        self.assertEqual(self.snapshot(), before)

    def test_running_it_twice_changes_nothing_more(self):
        self.plot("A-5")
        self.scramble_keys()
        self.run_step()
        once = self.snapshot()
        self.run_step()
        self.assertEqual(self.snapshot(), once)

    def test_with_no_rows_it_does_nothing(self):
        Estate.objects.all().delete()
        self.run_step()

    def test_a_collision_stops_it_before_anything_is_written_and_names_the_records(self):
        first = self.plot("A-5")
        self.scramble_keys()
        clash = self.plot("a-5")                                   # a real, distinct key: the constraint allows it
        Plot.objects.filter(pk=first.pk).update(import_batch=None)
        before = self.snapshot()
        with self.assertRaises(RuntimeError) as caught:
            self.run_step()
        message = str(caught.exception)
        self.assertIn(str(first.pk), message)
        self.assertIn(str(clash.pk), message)
        self.assertIn("'A-5'", message)
        self.assertIn("'a-5'", message)
        self.assertIn("Green Valley", message)
        self.assertIn("Nothing was changed", message)
        self.assertEqual(self.snapshot(), before)                  # no key was touched, no record renamed or deleted

    def test_an_estate_collision_is_reported_too(self):
        self.scramble_keys()
        clash = Estate.objects.create(company=self.a, name="green  valley")      # a real key: the constraint allows it
        before = self.snapshot()
        with self.assertRaises(RuntimeError) as caught:
            self.run_step()
        message = str(caught.exception)
        self.assertIn(f"company {self.a.pk}", message)
        self.assertIn(f"{self.estate.pk} 'Green Valley'", message)
        self.assertIn(f"{clash.pk} 'green  valley'", message)
        self.assertEqual(self.snapshot(), before)


    # ---- the backfill reads and writes in bounded batches ----

    def make_scrambled(self, plots=10):
        for n in range(plots):
            self.plot(f"P-{n}")
        Estate.objects.create(company=self.b, name="Royal Gardens")
        self.scramble_keys()

    def test_every_read_is_bounded_ordered_and_every_write_touches_only_the_key_column(self):
        self.make_scrambled(10)
        with mock.patch.object(backfill, "CHUNK", 3), CaptureQueriesContext(connection) as queries:
            self.run_step()
        sql = [q["sql"] for q in queries.captured_queries]
        plot_selects = [q for q in sql if q.lstrip().upper().startswith("SELECT") and "tracker_plot" in q]
        self.assertTrue(plot_selects)
        self.assertTrue(all("LIMIT 3" in q and "ORDER BY" in q for q in plot_selects), plot_selects)
        updates = [q for q in sql if q.lstrip().upper().startswith("UPDATE")]
        plot_updates = [q for q in updates if "tracker_plot" in q]
        self.assertEqual(len(plot_updates), 4)                       # 10 stale plots in batches of 3, 3, 3 and 1
        for q in updates:
            self.assertNotIn("plot_number", q)
            self.assertNotIn("import_batch", q)
            self.assertNotIn('"name"', q)

    def test_the_result_does_not_depend_on_the_chunk_size(self):
        self.make_scrambled(7)
        self.run_step()
        expected = self.snapshot()                                    # the correct keys, written with the default chunk
        for chunk in (1, 2, 3, 500):
            self.scramble_keys()
            with mock.patch.object(backfill, "CHUNK", chunk):
                self.run_step()
            self.assertEqual(self.snapshot(), expected, f"chunk {chunk}")

    def test_a_second_run_writes_nothing_at_all(self):
        self.make_scrambled(6)
        self.run_step()
        with CaptureQueriesContext(connection) as queries:
            self.run_step()
        self.assertEqual([q["sql"] for q in queries.captured_queries
                          if q["sql"].lstrip().upper().startswith(("UPDATE", "INSERT", "DELETE"))], [])

    def test_a_collision_is_found_across_batch_boundaries_and_still_writes_nothing(self):
        for n in range(6):
            self.plot(f"P-{n}")
        self.scramble_keys()
        last = self.plot("p-0")                                      # collides with the first plot, in another batch
        before = self.snapshot()
        with mock.patch.object(backfill, "CHUNK", 2):
            with CaptureQueriesContext(connection) as queries, self.assertRaises(RuntimeError) as caught:
                self.run_step()
        self.assertIn(str(last.pk), str(caught.exception))
        self.assertEqual([q for q in queries.captured_queries if q["sql"].lstrip().upper().startswith("UPDATE")], [])
        self.assertEqual(self.snapshot(), before)


class FindKeyProblemsTests(SimpleTestCase):
    def test_clean_data_has_no_problems(self):
        self.assertEqual(backfill.find_key_problems([(1, 1, "A"), (2, 1, "B"), (3, 2, "a")], [(1, 1, "1"), (2, 1, "2")]),
                         [])

    def test_estate_collisions_are_per_company(self):
        problems = backfill.find_key_problems([(1, 1, "Palm Grove"), (2, 1, "palm  grove"), (3, 2, "PALM GROVE")], [])
        self.assertEqual(len(problems), 1)
        self.assertIn("company 1", problems[0])
        self.assertIn("1 'Palm Grove'", problems[0])
        self.assertIn("2 'palm  grove'", problems[0])

    def test_plot_collisions_are_per_estate_and_name_the_estate(self):
        problems = backfill.find_key_problems(
            [(1, 1, "Green Valley"), (2, 1, "Royal")], [(1, 1, "A-5"), (2, 1, "a-5"), (3, 2, "A-5")])
        self.assertEqual(len(problems), 1)
        self.assertIn("estate 1 ('Green Valley')", problems[0])

    def test_blank_values_are_reported(self):
        problems = backfill.find_key_problems([(1, 1, "  ")], [(7, 1, "")])
        self.assertEqual(len(problems), 2)
        self.assertIn("estate 1 has a blank name", problems[0])
        self.assertIn("plot 7 has a blank plot number", problems[1])

    def test_numbers_that_look_different_are_not_collisions(self):
        self.assertEqual(backfill.find_key_problems([], [(1, 1, "05"), (2, 1, "5"), (3, 1, "A-5"), (4, 1, "A5")]), [])


class MigrationConsistencyTests(TestCase):
    def test_the_model_changes_are_fully_covered_by_migrations(self):
        call_command("makemigrations", "--check", "--dry-run", stdout=StringIO(), stderr=StringIO())
