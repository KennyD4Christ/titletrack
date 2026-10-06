from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import PermissionDenied
from django.test import TestCase

from importer.commit import commit_batch, undo_batch, undo_blockers
from importer.errors import ImportStateError, StaleDryRun, UndoBlocked
from importer.reports import issues_csv, safe_cell
from importer.tests.helpers import D, make_batch, make_company_with_types
from importer.validation import validate_import
from tracker import services
from tracker.models import (
    Buyer, Company, Estate, ImportBatch, Obligation, ObligationEvent, Plot, Sale, User,
)


def row(estate="Green Valley", plot="A-1", buyer="Ada Obi", price=1000, paid=1000, sale=D(2026, 2, 3),
        full=D(2026, 2, 10), phone=None, deed=None, survey=None):
    return [estate, plot, buyer, price, paid, sale, full, phone, deed, survey]


class ImportBase(TestCase):
    def setUp(self):
        self.co, self.manager = make_company_with_types()
        self.deed = self.co.obligation_types.get(name="Deed of Assignment")

    def batch(self, rows, **kw):
        return make_batch(self.co, self.manager, rows, **kw)

    def dry_run(self, batch):
        report, _ = validate_import(batch)
        batch.report, batch.status = report, ImportBatch.Status.VALIDATED
        batch.save()
        return report

    def issues(self, report, level=None):
        return [i for i in report["issues"] if level is None or i["level"] == level]


class ValidationTests(ImportBase):
    def test_messy_file_gives_row_level_findings(self):
        existing_estate = Estate.objects.create(company=self.co, name="Green Valley")
        for plot_no, buyer_name in (("A-30", "Someone Else"), ("A-31", "Same Person")):
            plot = Plot.objects.create(company=self.co, estate=existing_estate, plot_number=plot_no)
            buyer = Buyer.objects.create(company=self.co, full_name=buyer_name)
            services.create_sale(self.co, plot, buyer, 1000, date(2026, 1, 1), 100)
        rows = [
            row(plot=12.0, price="₦25,000,000", paid="₦25,000,000", phone="0803 111 2222", deed="Done"),   # row 2 ok
            row(estate=" green  valley ", plot="A-13", buyer="Bayo", price=18000000, paid=9000000, full=None, deed="Done"),
            row(plot="A-14", price="TBC"),                                      # invalid price
            row(plot="A-20", buyer="First"),                                    # ok
            row(plot="a-20", buyer="Second"),                                   # duplicate of the row above (case-insensitive)
            row(plot="A-21", buyer=None),                                       # missing buyer
            [None] * 10,                                                        # blank row
            row(plot="A-30", buyer="Another", price=1000),                      # conflict: sold to someone else
            row(plot="A-31", buyer="same person", price=1000),                  # unchanged: already imported
            row(plot="A-40", price=1000, paid=1500),                            # paid more than price
            row(plot="A-41", paid=None),                                        # blank paid
            row(plot="A-42", sale=None, full=D(2026, 4, 1)),                    # sale date from date fully paid
            row(plot="A-43", sale="31/02/2026"),                                # impossible date
            row(plot="A-44", deed="Weird status"),                              # unmapped status
        ]
        report = self.dry_run(self.batch(rows))
        s = report["summary"]
        by_row = {}
        for i in report["issues"]:
            by_row.setdefault(i["row"], []).append(i)

        self.assertEqual(s["total_rows"], 13)                                   # the blank row is not counted
        self.assertEqual(s["unchanged"], 1)
        self.assertEqual(s["conflicts"], 1)
        self.assertEqual(s["new_estates"], [])                                  # " green  valley " matched the existing estate
        # sheet row numbers: header is row 1, so the first data row is row 2 and the blank row is row 8
        self.assertTrue(any("Invalid price" in i["message"] for i in by_row[4]))
        self.assertTrue(any("Duplicate of row 5" in i["message"] for i in by_row[6]))
        self.assertTrue(any(i["level"] == "error" for i in by_row[7]))          # missing buyer
        self.assertTrue(any(i["level"] == "conflict" and "Someone Else" in i["message"] for i in by_row[9]))
        self.assertNotIn(10, by_row)                                            # unchanged rows raise no issue
        self.assertTrue(any("more than the price" in i["message"] for i in by_row[11]))
        self.assertTrue(any("blank" in i["message"] for i in by_row[12]))
        self.assertTrue(any("Sale date is missing" in i["message"] for i in by_row[13]))
        self.assertTrue(any("Invalid date" in i["message"] for i in by_row[14]))
        self.assertTrue(any("has not paid in full" in i["message"] for i in by_row[3]))   # 'Done' ignored: not fully paid
        self.assertEqual([u["key"] for u in report["unmapped_statuses"]], ["weird status"])
        self.assertFalse(s["can_commit"])                                       # unmapped status blocks the import
        self.assertEqual(s["errors"], len([i for i in report["issues"] if i["level"] == "error"]))

    def test_new_estates_are_reported_and_variants_are_merged(self):
        report = self.dry_run(self.batch([row(estate="Royal Gardens", plot="1"), row(estate="ROYAL  gardens", plot="2")]))
        self.assertEqual(report["summary"]["new_estates"], ["Royal Gardens"])
        self.assertEqual(report["summary"]["to_import"], 2)
        self.assertTrue(report["summary"]["can_commit"])

    def test_mapping_problems_block_everything(self):
        batch = self.batch([row()], mapping={"estate": "Estate"})
        report, prepared = validate_import(batch)
        self.assertTrue(report["mapping_problems"])
        self.assertFalse(report["summary"]["can_commit"])
        self.assertEqual(prepared, [])

    def test_implausible_dates_and_amounts(self):
        report = self.dry_run(self.batch([
            row(plot="1", sale="01/01/1950"), row(plot="2", sale="01/01/2099"), row(plot="3", price=-5),
            row(plot="4", price=10 ** 15), row(plot="5", paid=-1), row(plot="6", estate="x" * 300)]))
        self.assertEqual(report["summary"]["to_import"], 0)
        self.assertEqual(report["summary"]["errors"], 6)

    def test_validation_never_writes(self):
        before = (Estate.objects.count(), Plot.objects.count(), Buyer.objects.count(), Sale.objects.count())
        validate_import(self.batch([row(estate="New One")]))
        self.assertEqual(before, (Estate.objects.count(), Plot.objects.count(), Buyer.objects.count(), Sale.objects.count()))

    def test_csv_report_neutralises_formulas(self):
        import csv, io
        batch = self.batch([row(plot="1", price="-5x"), row(plot="2", price="+1+1"), row(plot="3", price="@cmd")])
        self.dry_run(batch)
        cells = [c for r in csv.reader(io.StringIO(issues_csv(batch))) for c in r]
        self.assertIn("'-5x", cells)
        self.assertIn("'+1+1", cells)
        self.assertIn("'@cmd", cells)
        self.assertFalse([c for c in cells if c.startswith(("=", "+", "-", "@"))])     # nothing a spreadsheet would run
        self.assertEqual(safe_cell("=SUM(A1)"), "'=SUM(A1)")
        self.assertEqual(safe_cell("normal"), "normal")


class CommitTests(ImportBase):
    def good_rows(self):
        return [
            row(plot=12.0, buyer="Ada Obi", price="₦25,000,000", paid="₦25,000,000", phone="0803 111 2222",
                deed="Done", survey="Pending"),
            row(estate=" green valley ", plot="A-13", buyer="Bayo Lawal", price=18000000, paid=9000000, full=None,
                sale=D(2026, 3, 1)),
            row(estate="Royal Gardens", plot="R-1", buyer="Ada Obi", price=9000000, paid=9000000, phone="08031112222",
                deed="With lawyer", full=D(2026, 5, 20), sale=D(2026, 5, 1)),
        ]

    def committed(self, rows=None):
        Estate.objects.create(company=self.co, name="Green Valley")
        batch = self.batch(rows or self.good_rows())
        report = self.dry_run(batch)
        self.assertTrue(report["summary"]["can_commit"], report)
        counts = commit_batch(batch, self.manager)
        batch.refresh_from_db()
        return batch, counts

    def test_commit_creates_everything_with_correct_dates_and_statuses(self):
        batch, counts = self.committed()
        self.assertEqual(counts["sales_created"], 3)
        self.assertEqual(counts["estates_created"], 1)                       # Royal Gardens; Green Valley was matched
        self.assertEqual(Estate.objects.filter(company=self.co).count(), 2)
        self.assertEqual(counts["buyers_created"], 2)                        # Ada Obi twice, same name + phone digits -> one buyer
        ada_sale = Sale.objects.get(plot__plot_number="12")
        self.assertEqual(ada_sale.price, Decimal("25000000.00"))
        self.assertEqual(ada_sale.fully_paid_on, date(2026, 2, 10))
        self.assertEqual(ada_sale.import_batch, batch)
        deed = ada_sale.obligations.get(obligation_type=self.deed)
        self.assertEqual((deed.started_on, deed.status), (date(2026, 2, 10), "completed"))
        self.assertEqual(deed.due_date, date(2026, 2, 10) + __import__("datetime").timedelta(days=90))
        self.assertEqual(ada_sale.obligations.get(obligation_type__name="Survey Plan").status, "not_started")
        event = deed.events.last()
        self.assertEqual(event.actor, self.manager)
        self.assertEqual(event.note, "Status set from import: register.xlsx")
        partial = Sale.objects.get(plot__plot_number="A-13")
        self.assertIsNone(partial.fully_paid_on)
        self.assertFalse(partial.obligations.filter(obligation_type=self.deed).exists())     # not fully paid yet
        self.assertEqual(Sale.objects.get(plot__plot_number="R-1").obligations.get(obligation_type=self.deed).status,
                         "awaiting_external")
        self.assertEqual(batch.status, ImportBatch.Status.COMMITTED)
        self.assertIsNotNone(batch.committed_at)

    def test_imported_obligations_show_up_as_incomplete_work(self):
        self.committed()
        open_ids = services.open_obligations(self.co)
        self.assertTrue(open_ids.filter(owner__isnull=True).exists())

    def test_commit_is_all_or_nothing(self):
        Estate.objects.create(company=self.co, name="Green Valley")
        batch = self.batch(self.good_rows())
        self.dry_run(batch)
        calls = {"n": 0}
        real = services.create_sale

        def flaky(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 3:
                raise RuntimeError("boom")
            return real(*args, **kwargs)

        with patch("importer.commit.services.create_sale", side_effect=flaky):
            with self.assertRaises(RuntimeError):
                commit_batch(batch, self.manager)
        self.assertEqual(Sale.objects.count(), 0)
        self.assertEqual(Obligation.objects.count(), 0)
        self.assertEqual(Estate.objects.filter(company=self.co).count(), 1)     # only the pre-existing estate
        batch.refresh_from_db()
        self.assertEqual(batch.status, ImportBatch.Status.VALIDATED)

    def test_commit_requires_a_current_dry_run(self):
        batch = self.batch(self.good_rows())
        with self.assertRaises(ImportStateError):                               # no dry run yet
            commit_batch(batch, self.manager)
        Estate.objects.create(company=self.co, name="Green Valley")
        self.dry_run(batch)
        plot = Plot.objects.create(company=self.co, estate=Estate.objects.get(name="Green Valley"), plot_number="A-13")
        services.create_sale(self.co, plot, Buyer.objects.create(company=self.co, full_name="Late Arrival"), 5, date(2026, 1, 1))
        with self.assertRaises(StaleDryRun):                                    # a conflicting sale appeared since the dry run
            commit_batch(batch, self.manager)

    def test_unmapped_statuses_block_the_commit(self):
        batch = self.batch([row(deed="Weird")])
        self.dry_run(batch)
        with self.assertRaises(ImportStateError):
            commit_batch(batch, self.manager)

    def test_other_company_cannot_commit_or_undo(self):
        batch = self.batch(self.good_rows())
        self.dry_run(batch)
        _, outsider = make_company_with_types("B Homes")
        with self.assertRaises(PermissionDenied):
            commit_batch(batch, outsider)

    def test_reimporting_the_same_file_changes_nothing(self):
        self.committed()
        again = self.batch(self.good_rows(), filename="register-again.xlsx")
        report = self.dry_run(again)
        self.assertEqual(report["summary"]["to_import"], 0)
        self.assertEqual(report["summary"]["unchanged"], 3)
        self.assertFalse(report["summary"]["can_commit"])
        with self.assertRaises(ImportStateError):
            commit_batch(again, self.manager)
        self.assertEqual(Sale.objects.count(), 3)

    def test_undo_removes_only_what_the_import_created(self):
        pre_estate = Estate.objects.create(company=self.co, name="Green Valley")
        pre_buyer = Buyer.objects.create(company=self.co, full_name="Existing Buyer")
        batch = self.batch(self.good_rows())
        self.dry_run(batch)
        commit_batch(batch, self.manager)
        batch.refresh_from_db()
        self.assertEqual(undo_blockers(batch), [])
        undo_batch(batch, self.manager)
        batch.refresh_from_db()
        self.assertEqual(batch.status, ImportBatch.Status.UNDONE)
        self.assertEqual((Sale.objects.count(), Plot.objects.count(), Obligation.objects.count()), (0, 0, 0))
        self.assertEqual(ObligationEvent.objects.count(), 0)
        self.assertEqual(Buyer.objects.count(), 1)                              # the pre-existing buyer survives
        self.assertTrue(Estate.objects.filter(pk=pre_estate.pk).exists())       # the pre-existing estate survives
        self.assertFalse(Estate.objects.filter(name="Royal Gardens").exists())  # the imported estate is gone
        self.assertTrue(Buyer.objects.filter(pk=pre_buyer.pk).exists())
        with self.assertRaises(ImportStateError):                               # cannot undo twice
            undo_batch(batch, self.manager)

    def test_undo_is_blocked_after_someone_edits_an_imported_obligation(self):
        batch, _ = self.committed()
        obligation = Obligation.objects.filter(sale__import_batch=batch).first()
        services.update_obligation(obligation, self.manager, note="Called the solicitor")
        with self.assertRaises(UndoBlocked) as ctx:
            undo_batch(batch, self.manager)
        self.assertIn("edited after the import", ctx.exception.reasons[0])
        self.assertEqual(Sale.objects.count(), 3)                               # nothing was deleted

    def test_reimport_after_undo_works(self):
        batch, _ = self.committed()
        undo_batch(batch, self.manager)
        again = self.batch(self.good_rows(), filename="second-try.xlsx")
        self.assertTrue(self.dry_run(again)["summary"]["can_commit"])
        commit_batch(again, self.manager)
        self.assertEqual(Sale.objects.count(), 3)

    def test_existing_unsold_plot_is_reused_not_duplicated(self):
        estate = Estate.objects.create(company=self.co, name="Green Valley")
        Plot.objects.create(company=self.co, estate=estate, plot_number="A-13")
        batch = self.batch([row(plot="a-13")])
        self.dry_run(batch)
        commit_batch(batch, self.manager)
        self.assertEqual(Plot.objects.filter(estate=estate).count(), 1)
        self.assertEqual(Sale.objects.count(), 1)

    def test_same_name_without_phone_creates_separate_buyers(self):
        batch = self.batch([row(plot="1", buyer="Ada Obi"), row(plot="2", buyer="Ada Obi")])
        self.dry_run(batch)
        counts = commit_batch(batch, self.manager)
        self.assertEqual(counts["buyers_created"], 2)                           # never merge people on name alone
