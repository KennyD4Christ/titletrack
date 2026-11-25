"""The importer and the estate/plot inventory (inventory Phase 1, stage 2).

Covers: an import into registered estates and plots; the importer surviving the canonical-key constraint when
another writer creates the same estate or plot in the gap (it uses the winner instead of failing); and the three
reasons import undo must refuse because of the inventory.
"""
from unittest import mock

from django.db import IntegrityError
from django.test import TestCase

from importer import commit as importer_commit
from importer.commit import commit_batch, undo_batch, undo_blockers
from importer.errors import UndoBlocked
from importer.tests.test_validation_and_commit import ImportBase, row
from tracker import inventory_services as inv
from tracker.models import Estate, ImportBatch, InventoryEvent, Plot, Sale


def blind_once(manager, matches):
    """The first manager.filter call whose keyword names are exactly `matches` sees nothing, as if the other
    writer had not committed yet. Every other call is real."""
    real, state = manager.filter, {"blinded": False}

    def filter_(*args, **kwargs):
        if not state["blinded"] and set(kwargs) == set(matches):
            state["blinded"] = True
            return manager.none()
        return real(*args, **kwargs)

    return mock.patch.object(manager, "filter", side_effect=filter_)


class InventoryImportBase(ImportBase):
    def register(self, estate_name, numbers):
        estate = inv.create_estate(self.co, self.manager, name=estate_name)
        for number in numbers:
            inv.register_plot(self.co, self.manager, estate=estate, plot_number=number)
        return estate

    def run_import(self, rows):
        batch = self.batch(rows)
        report = self.dry_run(batch)
        self.assertTrue(report["summary"]["can_commit"], report)
        counts = commit_batch(batch, self.manager)
        batch.refresh_from_db()
        return batch, counts


class ImportIntoRegisteredInventoryTests(InventoryImportBase):
    def test_registered_estates_and_plots_are_reused_not_duplicated(self):
        estate = self.register("Green Valley", ["A-1", "A-2"])
        batch, counts = self.run_import([row(estate=" green  valley", plot="a-1"), row(plot="A-3", buyer="Bayo Lawal")])
        self.assertEqual((counts["estates_created"], counts["plots_created"], counts["sales_created"]), (0, 1, 2))
        self.assertEqual(Estate.objects.filter(company=self.co).count(), 1)
        self.assertEqual(sorted(Plot.objects.filter(estate=estate).values_list("plot_number", flat=True)),
                         ["A-1", "A-2", "A-3"])
        self.assertEqual(Sale.objects.get(plot__plot_number="A-1").plot.import_batch, None)     # not an import plot
        self.assertEqual(Plot.objects.get(plot_number="A-3").import_batch, batch)

    def test_the_dry_run_treats_a_registered_unsold_plot_as_free(self):
        self.register("Green Valley", ["A-1"])
        report = self.dry_run(self.batch([row(plot="A-1")]))
        self.assertEqual((report["summary"]["to_import"], report["summary"]["conflicts"]), (1, 0))
        self.assertEqual(report["summary"]["new_estates"], [])

    def test_a_registered_plot_that_is_sold_is_still_a_conflict(self):
        estate = self.register("Green Valley", ["A-1"])
        self.run_import([row(plot="A-1", buyer="Ada Obi")])
        report = self.dry_run(self.batch([row(plot="a-1", buyer="Somebody Else")]))
        self.assertEqual((report["summary"]["to_import"], report["summary"]["conflicts"]), (0, 1))
        self.assertEqual(Plot.objects.filter(estate=estate).count(), 1)

    def test_undo_leaves_registered_estates_and_plots_alone_and_removes_what_the_import_created(self):
        estate = self.register("Green Valley", ["A-1"])
        batch, _ = self.run_import([row(plot="A-1"), row(plot="A-9", buyer="Bayo Lawal")])
        undo_batch(batch, self.manager)
        self.assertEqual(Sale.objects.count(), 0)
        self.assertTrue(Estate.objects.filter(pk=estate.pk).exists())
        self.assertEqual(list(Plot.objects.filter(estate=estate).values_list("plot_number", flat=True)), ["A-1"])


class ImportSurvivesTheCanonicalKeyConstraintTests(InventoryImportBase):
    def test_an_estate_created_by_another_writer_in_the_gap_is_used_not_a_failure(self):
        winner = Estate.objects.create(company=self.co, name="Green Valley")
        batch = self.batch([row(estate="green valley", plot="A-1")])
        self.assertTrue(self.dry_run(batch)["summary"]["can_commit"])
        with blind_once(Estate.objects, {"company"}):          # commit_batch's own estate lookup sees nothing
            counts = commit_batch(batch, self.manager)
        self.assertEqual(counts["estates_created"], 0)
        self.assertEqual(Estate.objects.filter(company=self.co).count(), 1)
        self.assertEqual(Sale.objects.get().plot.estate, winner)
        self.assertIsNone(Estate.objects.get(pk=winner.pk).import_batch)         # not ours, so undo will not touch it

    def test_a_plot_created_by_another_writer_in_the_gap_is_used_not_a_failure(self):
        estate = Estate.objects.create(company=self.co, name="Green Valley")
        winner = Plot.objects.create(company=self.co, estate=estate, plot_number="A-1")
        batch = self.batch([row(plot="a-1")])
        self.assertTrue(self.dry_run(batch)["summary"]["can_commit"])
        with blind_once(Plot.objects, {"estate"}):
            counts = commit_batch(batch, self.manager)
        self.assertEqual(counts["plots_created"], 0)
        self.assertEqual(Plot.objects.filter(estate=estate).count(), 1)
        self.assertEqual(Sale.objects.get().plot, winner)

    def test_the_winner_is_used_for_every_later_row_of_the_same_import(self):
        Estate.objects.create(company=self.co, name="Green Valley")
        batch = self.batch([row(estate="Green Valley", plot="A-1"), row(estate="GREEN VALLEY", plot="A-2", buyer="B L")])
        self.dry_run(batch)
        with blind_once(Estate.objects, {"company"}):
            commit_batch(batch, self.manager)
        self.assertEqual(Estate.objects.filter(company=self.co).count(), 1)
        self.assertEqual(Sale.objects.count(), 2)

    def test_an_integrity_error_that_is_not_a_key_clash_is_not_swallowed_and_nothing_is_imported(self):
        batch = self.batch([row(plot="A-1")])
        self.dry_run(batch)
        with mock.patch.object(Estate.objects, "create", side_effect=IntegrityError("something else")):
            with self.assertRaises(IntegrityError):
                commit_batch(batch, self.manager)
        batch.refresh_from_db()
        self.assertEqual(batch.status, ImportBatch.Status.VALIDATED)
        self.assertEqual((Estate.objects.count(), Plot.objects.count(), Sale.objects.count()), (0, 0, 0))

    def test_a_normal_import_still_reports_what_it_created(self):
        _, counts = self.run_import([row(estate="Royal Gardens", plot="R-1"), row(estate="Royal Gardens", plot="R-2",
                                                                                   buyer="Bayo Lawal")])
        self.assertEqual((counts["estates_created"], counts["plots_created"]), (1, 2))


class UndoBlockerTests(InventoryImportBase):
    def imported(self, rows=None):
        return self.run_import(rows or [row(estate="Royal Gardens", plot="R-1"),
                                        row(estate="Royal Gardens", plot="R-2", buyer="Bayo Lawal")])[0]

    def assert_blocked(self, batch, fragment):
        reasons = undo_blockers(batch)
        self.assertTrue(any(fragment in r for r in reasons), reasons)
        before = (Sale.objects.count(), Plot.objects.count(), Estate.objects.count())
        with self.assertRaises(UndoBlocked):
            undo_batch(batch, self.manager)
        self.assertEqual((Sale.objects.count(), Plot.objects.count(), Estate.objects.count()), before)

    # ---- blocker 3: curated after the import ----

    def test_renaming_an_imported_plot_after_the_import_blocks_undo(self):
        batch = self.imported()
        inv.rename_plot(Plot.objects.get(plot_number="R-1"), self.manager, plot_number="R-1A", reason="Survey correction")
        self.assert_blocked(batch, "renamed or edited after the import")

    def test_editing_an_imported_estate_after_the_import_blocks_undo(self):
        batch = self.imported()
        inv.edit_estate(Estate.objects.get(name="Royal Gardens"), self.manager, location="Lekki")
        self.assert_blocked(batch, "renamed or edited after the import")

    def test_the_reason_counts_each_edited_record_once(self):
        batch = self.imported()
        plot = Plot.objects.get(plot_number="R-1")
        inv.rename_plot(plot, self.manager, plot_number="R-1A", reason="first fix")
        inv.rename_plot(plot, self.manager, plot_number="R-1B", reason="second fix")
        inv.edit_estate(Estate.objects.get(name="Royal Gardens"), self.manager, location="Lekki")
        self.assertIn("2 imported estate(s) or plot(s) were renamed or edited", undo_blockers(batch)[0])

    def test_editing_records_the_import_did_not_create_is_not_a_blocker(self):
        registered = self.register("Green Valley", ["A-5"])
        batch = self.imported()
        inv.rename_plot(Plot.objects.get(plot_number="A-5"), self.manager, plot_number="A-6")
        inv.edit_estate(registered, self.manager, location="Ajah")
        self.assertEqual(undo_blockers(batch), [])
        undo_batch(batch, self.manager)

    def test_registering_new_plots_in_an_imported_estate_is_not_a_blocker_and_they_survive_undo(self):
        batch = self.imported()
        estate = Estate.objects.get(name="Royal Gardens")
        inv.register_plot(self.co, self.manager, estate=estate, plot_number="R-9")
        entries = [{"labels": ["R-10"]}]
        preview = inv.preview_bulk(self.co, self.manager, estate=estate, entries=entries)
        inv.commit_bulk(self.co, self.manager, estate=estate, entries=entries,
                        input_fingerprint_sent=preview["input_fingerprint"],
                        preview_fingerprint_sent=preview["preview_fingerprint"])
        self.assertEqual(undo_blockers(batch), [])
        undo_batch(batch, self.manager)
        self.assertTrue(Estate.objects.filter(pk=estate.pk).exists())                   # it gained other plots: kept
        self.assertEqual(sorted(Plot.objects.filter(estate=estate).values_list("plot_number", flat=True)),
                         ["R-10", "R-9"])
        self.assertEqual(Sale.objects.count(), 0)

    def test_undo_still_works_when_history_points_at_the_records_it_deletes(self):
        batch = self.imported()
        estate = Estate.objects.get(name="Royal Gardens")
        inv.register_plot(self.co, self.manager, estate=estate, plot_number="R-9")
        inv.remove_plot(Plot.objects.get(plot_number="R-9"), self.manager, reason="registered by mistake")
        undo_batch(batch, self.manager)
        self.assertFalse(Estate.objects.filter(name="Royal Gardens").exists())
        kept = InventoryEvent.objects.filter(estate_name="Royal Gardens")
        self.assertEqual(kept.count(), 2)
        self.assertTrue(all(event.estate is None for event in kept))

    def test_an_import_nobody_touched_is_not_blocked(self):
        self.assertEqual(undo_blockers(self.imported()), [])

    # ---- blockers 1 and 2: protected references (found from the models, so they cover site plans later) ----

    def test_the_scan_finds_protected_references_and_ignores_the_rest(self):
        batch = self.imported()
        plot_ids = list(Plot.objects.filter(import_batch=batch).values_list("pk", flat=True))
        inv.register_plot(self.co, self.manager, estate=Estate.objects.get(name="Royal Gardens"), plot_number="R-9")
        self.assertEqual(importer_commit._protected_references(Plot, plot_ids), [("sales", 2)])          # PROTECT
        self.assertEqual(importer_commit._protected_references(Plot, plot_ids, ignore={Sale}), [])        # handled elsewhere
        estate_ids = list(Estate.objects.values_list("pk", flat=True))
        self.assertEqual(importer_commit._protected_references(Estate, estate_ids, ignore={Plot}), [])
        self.assertEqual(importer_commit._protected_references(Plot, []), [])
        # inventory events point at plots and estates too, with SET_NULL: they are never a reason to refuse
        self.assertTrue(InventoryEvent.objects.filter(estate__isnull=False).exists())

    def test_protected_references_to_imported_plots_and_estates_block_undo_and_delete_nothing(self):
        batch = self.imported()

        def fake(model, ids, ignore=()):
            return [("site plan regions", 3)] if model is Plot else [("site plans", 1)]

        with mock.patch.object(importer_commit, "_protected_references", side_effect=fake):
            reasons = undo_blockers(batch)
            self.assertIn("3 site plan regions refer to plots this import created, so those plots cannot be removed.",
                          reasons)
            self.assertIn("1 site plans refer to estates this import created, so those estates cannot be removed.",
                          reasons)
            with self.assertRaises(UndoBlocked):
                undo_batch(batch, self.manager)
        self.assertEqual((Sale.objects.count(), Plot.objects.count()), (2, 2))

    def test_only_the_estates_undo_would_delete_are_scanned(self):
        self.register("Green Valley", ["A-5"])
        batch = self.imported([row(estate="Green Valley", plot="G-1"), row(estate="Royal Gardens", plot="R-1", buyer="B L")])
        royal = Estate.objects.get(name="Royal Gardens")
        green = Estate.objects.get(name="Green Valley")
        calls, real = [], importer_commit._protected_references

        def spy(model, ids, ignore=()):
            ids = list(ids)
            calls.append((model, ids))
            return real(model, ids, ignore)

        with mock.patch.object(importer_commit, "_protected_references", side_effect=spy):
            undo_blockers(batch)
        estate_calls = [ids for model, ids in calls if model is Estate]
        self.assertEqual(estate_calls, [[royal.pk]])                      # Green Valley keeps A-5, so it is not scanned
        self.assertNotIn(green.pk, estate_calls[0])
