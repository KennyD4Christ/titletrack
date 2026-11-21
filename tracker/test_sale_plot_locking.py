"""Sale creation and the plot inventory share one row-lock protocol (Stage 2 review).

create_sale locks the plot FOR NO KEY UPDATE and re-reads it; rename_plot and remove_plot lock it FOR UPDATE. The
two conflict, so a sale is recorded either before a rename or removal (which then sees it) or not at all (it finds
the plot renamed or gone and is refused with PlotChanged, nothing written).

Two groups of tests:
  - deterministic ones, on any database, for the checks themselves (stale instances, wrong company, ...);
  - PostgreSQL ones, with separate connections, where the ORDER is established for real: one connection holds its
    transaction open while another is started, the test waits until pg_stat_activity shows that the second is
    blocked on a row lock, and only then lets the first commit. Nothing about locking is mocked.
"""
import threading
import time
from datetime import date
from unittest import mock, skipUnless

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, connection, transaction
from django.test import TestCase, TransactionTestCase

from api.views.sales_desk import _refusal
from importer.commit import commit_batch
from importer.errors import StaleDryRun
from importer.tests.helpers import make_batch, make_company_with_types
from importer.tests.test_validation_and_commit import row
from importer.validation import validate_import
from tracker import inventory_services as inv
from tracker import services
from tracker.models import (
    Buyer, Company, Estate, ImportBatch, InventoryEvent, ObligationType, Payment, Plot, Sale, SaleEvent, User,
)


class PlotBase(TestCase):
    def setUp(self):
        self.a = Company.objects.create(name="A Homes")
        self.b = Company.objects.create(name="B Homes")
        self.mgr = User.objects.create_user("mgr_a", password="x", company=self.a, role="manager")
        self.mgr_b = User.objects.create_user("mgr_b", password="x", company=self.b, role="manager")
        ObligationType.objects.create(company=self.a, name="Allocation Letter", trigger="on_sale", sla_days=30)
        self.estate = inv.create_estate(self.a, self.mgr, name="Green Valley")
        self.plot = inv.register_plot(self.a, self.mgr, estate=self.estate, plot_number="A-5")
        self.buyer = Buyer.objects.create(company=self.a, full_name="Ada Obi")

    def sell(self, plot, company=None, **kwargs):
        return services.create_sale(company or self.a, plot, self.buyer, 1000, date(2026, 1, 5), **kwargs)

    def add_sale(self, plot_number="A-5", **kwargs):
        return services.add_sale(self.a, self.mgr, plot_number=plot_number, price="1000", sale_date=date.today(),
                                 estate=self.estate, buyer_name="New Buyer", **kwargs)

    def assert_nothing_written(self):
        self.assertEqual((Sale.objects.count(), Payment.objects.count(), SaleEvent.objects.count()), (0, 0, 0))


# ---------- deterministic: what create_sale does with the plot it is given ----------

class CreateSaleLocksAndVerifiesTests(PlotBase):
    def test_a_current_plot_sells_exactly_as_before(self):
        sale = services.create_sale(self.a, self.plot, self.buyer, 1000, date(2026, 1, 5), 1000, None, actor=self.mgr)
        self.assertEqual((sale.plot_id, sale.company, sale.status), (self.plot.pk, self.a, "active"))
        self.assertEqual(sale.fully_paid_on, date(2026, 1, 5))
        opening = Payment.objects.get(sale=sale)
        self.assertEqual((opening.kind, opening.amount, opening.recorded_by), ("opening", 1000, self.mgr))
        self.assertEqual(SaleEvent.objects.filter(sale=sale, action="created").count(), 1)
        self.assertEqual(sale.obligations.count(), 1)                        # the on-sale obligation was generated

    def test_the_sale_is_attached_to_the_row_that_was_locked_and_re_read(self):
        stale_but_same_key = Plot.objects.get(pk=self.plot.pk)
        inv.rename_plot(self.plot, self.mgr, plot_number="a-5")              # only the letter case changed
        sale = self.sell(stale_but_same_key)
        self.assertEqual(sale.plot.plot_number, "a-5")                        # the fresh row, not the old instance

    def test_a_stale_plot_instance_that_was_renamed_is_refused_and_nothing_is_written(self):
        stale = Plot.objects.get(pk=self.plot.pk)
        inv.rename_plot(self.plot, self.mgr, plot_number="A-6")
        with self.assertRaises(services.PlotChanged) as caught:
            self.sell(stale)
        self.assertFalse(caught.exception.removed)
        self.assertIn("'A-5'", str(caught.exception))
        self.assertIn("'A-6'", str(caught.exception))
        self.assertIn("nothing was recorded", str(caught.exception))
        self.assert_nothing_written()
        self.assertEqual(Plot.objects.get(pk=self.plot.pk).plot_number, "A-6")

    def test_a_stale_plot_instance_that_was_removed_is_refused_not_a_database_error(self):
        stale = Plot.objects.get(pk=self.plot.pk)
        inv.remove_plot(self.plot, self.mgr, reason="registered by mistake")
        try:
            self.sell(stale)
        except services.PlotChanged as exc:
            self.assertTrue(exc.removed)
            self.assertIn("no longer exists", str(exc))
        except IntegrityError:
            self.fail("a removed plot must give PlotChanged, never a raw IntegrityError")
        else:
            self.fail("the sale was recorded against a plot that no longer exists")
        self.assert_nothing_written()

    def test_a_plot_deleted_behind_the_inventory_services_is_also_refused_cleanly(self):
        stale = Plot.objects.get(pk=self.plot.pk)
        Plot.objects.filter(pk=self.plot.pk).delete()                         # an operator in the shell
        with self.assertRaises(services.PlotChanged) as caught:
            self.sell(stale)
        self.assertTrue(caught.exception.removed)
        self.assert_nothing_written()

    def test_another_companys_plot_is_refused(self):
        estate_b = inv.create_estate(self.b, self.mgr_b, name="B Estate")
        plot_b = inv.register_plot(self.b, self.mgr_b, estate=estate_b, plot_number="B-1")
        with self.assertRaises(PermissionDenied):
            self.sell(plot_b)                                                 # company A, plot of company B
        with self.assertRaises(PermissionDenied):
            self.sell(self.plot, company=self.b)                              # company B, plot of company A
        self.assert_nothing_written()

    def test_an_unsaved_plot_is_refused(self):
        with self.assertRaises(ValueError):
            self.sell(Plot(company=self.a, estate=self.estate, plot_number="Z-1"))
        self.assert_nothing_written()

    def test_an_expected_plot_number_is_checked_against_the_locked_row(self):
        for typed in ("A-5", "a-5", "  A-5  ", "a-5\t"):
            Sale.objects.all().delete()
            self.assertEqual(self.sell(self.plot, expected_plot_number=typed).plot_id, self.plot.pk)
        Sale.objects.all().delete()
        with self.assertRaises(services.PlotChanged) as caught:
            self.sell(self.plot, expected_plot_number="A-6")
        self.assertFalse(caught.exception.removed)
        self.assertIn("'A-6'", str(caught.exception))
        self.assert_nothing_written()

    def test_an_occupied_plot_still_ends_in_the_databases_integrity_error_for_a_direct_call(self):
        self.sell(self.plot)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.sell(self.plot)
        self.assertEqual(Sale.objects.filter(plot=self.plot, status="active").count(), 1)


class AddSalePlotChangedTests(PlotBase):
    """add_sale looks the plot up, then create_sale locks it. A plot changed in between ends in PlotChanged and the
    whole add_sale is rolled back (no buyer, no sale). The stale lookup is injected deterministically."""

    def stale_lookup(self):
        stale = Plot.objects.get(pk=self.plot.pk)
        return mock.patch.object(services, "_get_or_create_plot", return_value=stale)

    def test_a_plot_renamed_after_the_lookup_is_refused_and_nothing_is_kept(self):
        buyers = Buyer.objects.count()
        with self.stale_lookup():
            inv.rename_plot(self.plot, self.mgr, plot_number="A-6")
            with self.assertRaises(services.PlotChanged) as caught:
                self.add_sale("A-5")
        self.assertFalse(caught.exception.removed)
        self.assertIn("now numbered 'A-6'", str(caught.exception))
        self.assert_nothing_written()
        self.assertEqual(Buyer.objects.count(), buyers)                       # the new buyer was rolled back too
        self.assertEqual(Plot.objects.get(pk=self.plot.pk).plot_number, "A-6")

    def test_a_plot_removed_after_the_lookup_is_refused_not_a_database_error(self):
        buyers = Buyer.objects.count()
        with self.stale_lookup():
            inv.remove_plot(self.plot, self.mgr, reason="registered by mistake")
            with self.assertRaises(services.PlotChanged) as caught:
                self.add_sale("A-5")
        self.assertTrue(caught.exception.removed)
        self.assert_nothing_written()
        self.assertEqual((Buyer.objects.count(), Plot.objects.count()), (buyers, 0))   # nor was the plot recreated

    def test_the_refusal_is_a_409_with_the_message_the_person_needs(self):
        with self.stale_lookup():
            inv.rename_plot(self.plot, self.mgr, plot_number="A-6")
            with self.assertRaises(services.PlotChanged) as caught:
                self.add_sale("A-5")
        self.assertIsInstance(caught.exception, services.SaleStateError)
        response = _refusal(caught.exception)
        self.assertEqual(response.status_code, 409)
        self.assertIn("renamed while this sale was being prepared", response.data["detail"])

    def test_the_number_requested_is_what_is_checked_so_a_case_variant_still_sells(self):
        sale = self.add_sale("  a-5 ")
        self.assertEqual(sale.plot_id, self.plot.pk)

    def test_a_plot_taken_by_another_sale_is_still_plot_occupied(self):
        self.add_sale("A-5")
        with self.assertRaises(services.PlotOccupied):
            self.add_sale("a-5")


class ImportUsesTheSameProtocolTests(TestCase):
    """The importer calls create_sale, so it takes the same lock and makes the same check. A plot changed under it
    ends the import with StaleDryRun (the existing 409): nothing is imported."""

    def setUp(self):
        self.co, self.manager = make_company_with_types()
        self.estate = inv.create_estate(self.co, self.manager, name="Green Valley")
        self.plot = inv.register_plot(self.co, self.manager, estate=self.estate, plot_number="A-1")
        self.batch = make_batch(self.co, self.manager, [row(plot="A-1")])
        report, _ = validate_import(self.batch)
        self.batch.report, self.batch.status = report, ImportBatch.Status.VALIDATED
        self.batch.save()

    def disturb_then_sell(self, disturb):
        real = services.create_sale

        def create_sale(company, plot, *args, **kwargs):
            disturb(plot)
            return real(company, plot, *args, **kwargs)

        return mock.patch.object(services, "create_sale", side_effect=create_sale)

    def assert_import_left_nothing(self):
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.status, ImportBatch.Status.VALIDATED)
        self.assertEqual((Sale.objects.count(), Buyer.objects.count(), Payment.objects.count()), (0, 0, 0))

    def test_the_importer_passes_the_plot_number_each_row_named(self):
        with mock.patch.object(services, "create_sale", wraps=services.create_sale) as spy:
            commit_batch(self.batch, self.manager)
        self.assertEqual(spy.call_args.kwargs["expected_plot_number"], "A-1")
        self.assertEqual(Sale.objects.get().plot_id, self.plot.pk)

    def test_a_plot_renamed_under_the_import_stops_it_with_a_stale_dry_run(self):
        with self.disturb_then_sell(lambda plot: Plot.objects.filter(pk=plot.pk).update(plot_number="A-9")):
            with self.assertRaises(StaleDryRun) as caught:
                commit_batch(self.batch, self.manager)
        message = str(caught.exception)
        self.assertIn("Row 2", message)
        self.assertIn("now numbered 'A-9'", message)
        self.assertIn("Run the dry run again", message)
        self.assert_import_left_nothing()

    def test_a_plot_removed_under_the_import_stops_it_with_a_stale_dry_run(self):
        with self.disturb_then_sell(lambda plot: Plot.objects.filter(pk=plot.pk).delete()):
            with self.assertRaises(StaleDryRun) as caught:
                commit_batch(self.batch, self.manager)
        self.assertIn("no longer exists", str(caught.exception))
        self.assert_import_left_nothing()

    def test_an_ordinary_import_is_unchanged(self):
        counts = commit_batch(self.batch, self.manager)
        self.assertEqual((counts["sales_created"], counts["plots_created"], counts["estates_created"]), (1, 0, 0))


# ---------- PostgreSQL: the order is real ----------

class Held:
    """Runs `work` in its own thread, on its own connection, inside a transaction that stays open (so its locks
    stay held) until release() is called; then it commits."""

    def __init__(self, work):
        self.work = work
        self.result = self.error = None
        self._ready, self._release = threading.Event(), threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        try:
            with transaction.atomic():
                self.result = self.work()
                self._ready.set()
                self._release.wait(30)
        except Exception as exc:                  # recorded and asserted on by the test
            self.error = exc
        finally:
            self._ready.set()
            connection.close()

    def start(self):
        self._thread.start()
        assert self._ready.wait(15), "the held transaction did not get going"
        assert self.error is None, f"the held transaction failed: {self.error!r}"
        return self

    def release(self):
        """Let the transaction commit and wait for it. Safe to call more than once, and before start()."""
        self._release.set()
        if self._thread.ident is not None:
            self._thread.join(30)
            assert not self._thread.is_alive(), "the held transaction did not finish"


class Runner(threading.Thread):
    """Runs `work` on its own connection and records what it returned or raised."""

    def __init__(self, work):
        super().__init__(daemon=True)
        self.work, self.result, self.error = work, None, None

    def run(self):
        try:
            self.result = self.work()
        except Exception as exc:
            self.error = exc
        finally:
            connection.close()

    def finish(self):
        self.join(30)
        assert not self.is_alive(), "the runner did not finish"


def wait_until_blocked(*fragments, timeout=15):
    """Wait until PostgreSQL itself reports a backend waiting on a row lock in a statement containing every
    fragment. This is what proves the ORDER: the second transaction has reached the lock and is queued behind the
    first, which has not committed."""
    pattern = "%" + "%".join(fragments) + "%"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                "AND pid <> pg_backend_pid() AND wait_event_type = 'Lock' AND query LIKE %s", [pattern])
            if cursor.fetchone()[0]:
                return
        time.sleep(0.02)
    raise AssertionError(f"no transaction became blocked on a lock in a statement matching {fragments}")


@skipUnless(connection.vendor == "postgresql", "needs row-level locks and separate connections")
class SaleVersusInventoryConcurrencyTests(TransactionTestCase):
    SALE_LOCK = ("tracker_plot", "FOR NO KEY UPDATE")          # create_sale waiting for the plot
    INVENTORY_LOCK = ("tracker_plot", "FOR UPDATE")            # rename_plot / remove_plot waiting for the plot

    def setUp(self):
        self.a, self.mgr = make_company_with_types()
        self.estate = inv.create_estate(self.a, self.mgr, name="Green Valley")
        self.plot = inv.register_plot(self.a, self.mgr, estate=self.estate, plot_number="A-5")
        self.buyer = Buyer.objects.create(company=self.a, full_name="Ada Obi")
        self._held, self._running = [], []
        self.addCleanup(self._tidy)

    def _tidy(self):
        """Whatever happened in the test (including a failed assertion): let every held transaction commit first,
        then wait for the runners that were queued behind them. A failing test must never hang the suite."""
        for held in self._held:
            held.release()
        for runner in self._running:
            runner.join(30)

    def hold(self, work):
        held = Held(work)
        self._held.append(held)
        return held.start()

    def run_in_thread(self, work):
        runner = Runner(work)
        self._running.append(runner)
        runner.start()
        return runner

    def add_sale(self, buyer_name="New Buyer", plot_number="A-5"):
        return services.add_sale(self.a, self.mgr, plot_number=plot_number, price="1000", sale_date=date.today(),
                                 estate=self.estate, buyer_name=buyer_name)

    def create_sale(self, buyer=None):
        return services.create_sale(self.a, self.plot, buyer or self.buyer, 1000, date(2026, 1, 5), actor=self.mgr)

    def plot_state(self):
        return list(Plot.objects.filter(estate=self.estate).values_list("plot_number", flat=True))

    # ---- the rename or removal commits first; the sale, already prepared, must be refused ----

    def test_a_sale_prepared_before_a_rename_is_refused_after_it(self):
        rename = self.hold(lambda: inv.rename_plot(self.plot, self.mgr, plot_number="A-6"))
        sale = self.run_in_thread(self.add_sale)
        wait_until_blocked(*self.SALE_LOCK)                   # the sale found plot A-5, and waits on the rename
        self.assertTrue(sale.is_alive())
        rename.release()
        sale.finish()
        self.assertIsNone(rename.error)
        self.assertIsInstance(sale.error, services.PlotChanged, sale.error)
        self.assertFalse(sale.error.removed)
        self.assertEqual(self.plot_state(), ["A-6"])
        self.assertEqual(Sale.objects.count(), 0)            # it was NOT attached to the renamed plot
        self.assertEqual(Buyer.objects.count(), 1)           # and its new buyer was rolled back

    def test_a_sale_prepared_before_a_removal_is_refused_after_it_not_a_database_error(self):
        removal = self.hold(lambda: inv.remove_plot(self.plot, self.mgr, reason="registered by mistake"))
        sale = self.run_in_thread(self.add_sale)
        wait_until_blocked(*self.SALE_LOCK)
        self.assertTrue(sale.is_alive())
        removal.release()
        sale.finish()
        self.assertIsNone(removal.error)
        self.assertIsInstance(sale.error, services.PlotChanged, sale.error)       # not IntegrityError
        self.assertTrue(sale.error.removed)
        self.assertEqual((Plot.objects.count(), Sale.objects.count(), Buyer.objects.count()), (0, 0, 1))

    # ---- the sale commits first; the rename or removal must see it ----

    def test_a_rename_that_waited_for_a_sale_sees_it_and_asks_for_a_reason(self):
        sale = self.hold(self.create_sale)
        rename = self.run_in_thread(lambda: inv.rename_plot(self.plot, self.mgr, plot_number="A-6"))
        wait_until_blocked(*self.INVENTORY_LOCK)
        self.assertTrue(rename.is_alive())
        sale.release()
        rename.finish()
        self.assertIsNone(sale.error)
        self.assertIsInstance(rename.error, ValueError, rename.error)
        self.assertIn("reason", str(rename.error))           # it checked for sales AFTER the lock, and found one
        self.assertEqual((self.plot_state(), Sale.objects.count()), (["A-5"], 1))

    def test_a_rename_that_waited_for_a_sale_keeps_the_sale_on_the_plot(self):
        sale = self.hold(self.create_sale)
        rename = self.run_in_thread(lambda: inv.rename_plot(self.plot, self.mgr, plot_number="A-6", reason="Survey renumbering"))
        wait_until_blocked(*self.INVENTORY_LOCK)
        sale.release()
        rename.finish()
        self.assertIsNone(rename.error, rename.error)
        recorded = Sale.objects.get()
        self.assertEqual((recorded.plot_id, recorded.plot.plot_number), (self.plot.pk, "A-6"))
        self.assertEqual(InventoryEvent.objects.get(action="plot_renamed").note, "Survey renumbering")

    def test_a_removal_that_waited_for_a_sale_is_refused_cleanly(self):
        sale = self.hold(self.create_sale)
        removal = self.run_in_thread(lambda: inv.remove_plot(self.plot, self.mgr, reason="registered by mistake"))
        wait_until_blocked(*self.INVENTORY_LOCK)
        sale.release()
        removal.finish()
        self.assertIsNone(sale.error)
        self.assertIsInstance(removal.error, inv.RemovalBlocked, removal.error)    # not a ProtectedError
        self.assertEqual((self.plot_state(), Sale.objects.count()), (["A-5"], 1))

    # ---- two sales of one plot ----

    def test_two_sales_of_one_plot_are_serialised_and_the_second_gets_plot_occupied(self):
        first = self.hold(lambda: self.add_sale("First Buyer"))
        second = self.run_in_thread(lambda: self.add_sale("Second Buyer"))
        wait_until_blocked(*self.SALE_LOCK)
        self.assertTrue(second.is_alive())
        first.release()
        second.finish()
        self.assertIsNone(first.error)
        self.assertIsInstance(second.error, services.PlotOccupied, second.error)
        self.assertEqual(Sale.objects.filter(plot=self.plot, status="active").count(), 1)
        self.assertEqual(Sale.objects.get().buyer.full_name, "First Buyer")
        self.assertFalse(Buyer.objects.filter(full_name="Second Buyer").exists())

    def test_a_second_direct_create_sale_still_ends_in_the_integrity_error(self):
        first = self.hold(self.create_sale)
        second = self.run_in_thread(lambda: self.create_sale(Buyer.objects.create(company=self.a, full_name="Bayo Lawal")))
        wait_until_blocked(*self.SALE_LOCK)
        first.release()
        second.finish()
        self.assertIsInstance(second.error, IntegrityError, second.error)
        self.assertEqual(Sale.objects.count(), 1)

    # ---- the importer takes part in the same protocol ----

    def prepared_import(self):
        batch = make_batch(self.a, self.mgr, [row(estate="Green Valley", plot="A-5", buyer="Ada Obi")])
        report, _ = validate_import(batch)
        self.assertTrue(report["summary"]["can_commit"], report)
        batch.report, batch.status = report, ImportBatch.Status.VALIDATED
        batch.save()
        return batch

    def test_an_import_prepared_before_a_rename_stops_after_it(self):
        batch = self.prepared_import()
        rename = self.hold(lambda: inv.rename_plot(self.plot, self.mgr, plot_number="A-6"))
        imported = self.run_in_thread(lambda: commit_batch(batch, self.mgr))
        wait_until_blocked(*self.SALE_LOCK)                  # the import's create_sale is waiting on the plot
        self.assertTrue(imported.is_alive())
        rename.release()
        imported.finish()
        self.assertIsInstance(imported.error, StaleDryRun, imported.error)
        self.assertIn("Row 2", str(imported.error))
        self.assertIn("now numbered 'A-6'", str(imported.error))
        batch.refresh_from_db()
        self.assertEqual(batch.status, ImportBatch.Status.VALIDATED)
        self.assertEqual((Sale.objects.count(), Buyer.objects.count()), (0, 1))
        self.assertEqual(self.plot_state(), ["A-6"])

    def test_an_import_prepared_before_a_removal_stops_after_it(self):
        batch = self.prepared_import()
        removal = self.hold(lambda: inv.remove_plot(self.plot, self.mgr, reason="registered by mistake"))
        imported = self.run_in_thread(lambda: commit_batch(batch, self.mgr))
        wait_until_blocked(*self.SALE_LOCK)
        removal.release()
        imported.finish()
        self.assertIsInstance(imported.error, StaleDryRun, imported.error)
        self.assertIn("no longer exists", str(imported.error))
        self.assertEqual((Plot.objects.count(), Sale.objects.count()), (0, 0))

    def test_a_rename_waits_for_an_import_that_holds_the_plot_and_then_sees_its_sale(self):
        batch = self.prepared_import()
        imported = self.hold(lambda: commit_batch(batch, self.mgr))
        rename = self.run_in_thread(lambda: inv.rename_plot(self.plot, self.mgr, plot_number="A-6"))
        wait_until_blocked(*self.INVENTORY_LOCK)             # the rename queued behind the import's plot lock
        self.assertTrue(rename.is_alive())
        imported.release()
        rename.finish()
        self.assertIsNone(imported.error)
        self.assertIsInstance(rename.error, ValueError, rename.error)
        self.assertIn("reason", str(rename.error))
        self.assertEqual((self.plot_state(), Sale.objects.filter(import_batch=batch).count()), (["A-5"], 1))
