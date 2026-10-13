import threading
from datetime import timedelta
from decimal import Decimal
from unittest import skipUnless
from unittest.mock import patch

from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, connection, transaction
from django.db.models import RestrictedError
from django.test import RequestFactory, TestCase, TransactionTestCase
from django.utils import timezone

from importer.commit import commit_batch, undo_batch, undo_blockers
from importer.errors import UndoBlocked
from importer.tests.helpers import D, make_batch, make_company_with_types
from importer.validation import validate_import

from . import services
from .models import (
    Buyer, Company, Department, Estate, Obligation, ObligationType, Payment, Plot, Sale, SaleEvent, User,
)


class LedgerBase(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.sale_date = self.today - timedelta(days=30)
        self.co = Company.objects.create(name="A Homes")
        self.other = Company.objects.create(name="B Homes")
        self.legal = Department.objects.create(company=self.co, name="Legal")
        self.manager = User.objects.create_user("mike", password="x", company=self.co, role="manager")
        self.eve = User.objects.create_user("eve", password="x", company=self.other, role="manager")
        self.alloc = ObligationType.objects.create(company=self.co, name="Allocation Letter", trigger="on_sale", sla_days=30)
        self.deed = ObligationType.objects.create(company=self.co, name="Deed", trigger="on_full_payment",
                                                  sla_days=90, default_department=self.legal)
        self.estate = Estate.objects.create(company=self.co, name="Green Valley")
        self.n = 0

    def make_sale(self, price=1000, paid=0, paid_on=None, actor=None):
        self.n += 1
        plot = Plot.objects.create(company=self.co, estate=self.estate, plot_number=f"P-{self.n}")
        buyer = Buyer.objects.create(company=self.co, full_name=f"Buyer {self.n}")
        return services.create_sale(self.co, plot, buyer, price, self.sale_date, paid, paid_on, actor=actor)

    def fresh(self, sale):
        return Sale.objects.get(pk=sale.pk)

    def ledger(self, sale):
        return services.ledger_total(sale)

    def deeds(self, sale):
        return Obligation.objects.filter(sale=sale, obligation_type=self.deed)


class OpeningBalanceTests(LedgerBase):
    def test_create_sale_records_an_opening_balance_equal_to_the_total(self):
        sale = self.make_sale(price=1000, paid=400, actor=self.manager)
        row = sale.payments.get()
        self.assertEqual((row.kind, row.amount, row.paid_on, row.recorded_by),
                         ("opening", Decimal("400"), self.sale_date, self.manager))
        self.assertEqual(self.ledger(sale), self.fresh(sale).amount_paid)

    def test_nothing_paid_means_no_ledger_row(self):
        sale = self.make_sale(price=1000, paid=0)
        self.assertEqual(sale.payments.count(), 0)
        self.assertEqual(self.ledger(sale), 0)

    def test_an_overpaid_sale_keeps_its_exact_total(self):
        sale = self.make_sale(price=1000, paid=1500)
        self.assertEqual(self.fresh(sale).amount_paid, Decimal("1500"))
        self.assertEqual(self.ledger(sale), Decimal("1500"))

    def test_fully_paid_opening_uses_the_paid_date_and_creates_the_deed(self):
        sale = self.make_sale(price=1000, paid=1000, paid_on=self.today - timedelta(days=10))
        self.assertEqual(sale.payments.get().paid_on, self.today - timedelta(days=10))
        self.assertEqual(self.fresh(sale).fully_paid_on, self.today - timedelta(days=10))
        self.assertEqual(self.deeds(sale).count(), 1)

    def test_creation_is_recorded_in_the_sale_history(self):
        sale = self.make_sale(price=1000, paid=400, actor=self.manager)
        event = sale.events.get()
        self.assertEqual((event.action, event.actor), ("created", self.manager))
        self.assertEqual(event.changes["amount_paid"], [None, "400.00"])

    def test_legacy_total_setter_writes_an_adjustment_and_keeps_its_behaviour(self):
        sale = self.make_sale(price=1000, paid=400)
        services.register_payment_total(sale, 1000, paid_on=self.today - timedelta(days=3))
        fresh = self.fresh(sale)
        self.assertEqual((fresh.amount_paid, fresh.fully_paid_on), (Decimal("1000"), self.today - timedelta(days=3)))
        adjustment = sale.payments.get(kind="adjustment")
        self.assertEqual(adjustment.amount, Decimal("600"))
        self.assertEqual(self.ledger(sale), fresh.amount_paid)
        self.assertEqual(self.deeds(sale).count(), 1)
        services.register_payment_total(sale, 1000)                       # unchanged total: nothing new
        self.assertEqual(sale.payments.filter(kind="adjustment").count(), 1)

    def test_legacy_total_setter_still_works_on_a_cancelled_sale_without_new_obligations(self):
        sale = self.make_sale(price=1000, paid=100)
        services.cancel_sale(sale, self.manager, "defaulted")
        services.register_payment_total(sale, 1000)
        self.assertFalse(self.deeds(sale).exists())
        self.assertEqual(self.ledger(sale), self.fresh(sale).amount_paid)


class RecordPaymentTests(LedgerBase):
    def test_a_partial_payment_updates_the_total_and_creates_no_obligation(self):
        sale = self.make_sale(price=1000, paid=400)
        result = services.record_payment(sale, self.manager, 300, reference="TRF-1")
        fresh = self.fresh(sale)
        self.assertEqual(fresh.amount_paid, Decimal("700"))
        self.assertIsNone(fresh.fully_paid_on)
        self.assertEqual(result.obligations_created, [])
        self.assertFalse(result.duplicate)
        self.assertEqual((result.payment.kind, result.payment.amount, result.payment.recorded_by, result.payment.reference),
                         ("payment", Decimal("300"), self.manager, "TRF-1"))
        self.assertFalse(self.deeds(sale).exists())
        self.assertEqual(self.ledger(sale), fresh.amount_paid)
        event = sale.events.get(action="payment")
        self.assertEqual((event.actor, event.changes["amount_paid"]), (self.manager, ["400.00", "700.00"]))

    def test_the_payment_that_completes_the_price_creates_the_obligations_and_reports_them(self):
        sale = self.make_sale(price=1000, paid=400)
        paid_on = self.today - timedelta(days=5)
        result = services.record_payment(sale, self.manager, 600, paid_on=paid_on)
        fresh = self.fresh(sale)
        self.assertEqual((fresh.amount_paid, fresh.fully_paid_on), (Decimal("1000"), paid_on))
        self.assertEqual([o.obligation_type.name for o in result.obligations_created], ["Deed"])
        deed = self.deeds(sale).get()
        self.assertEqual((deed.started_on, deed.due_date, deed.department), (paid_on, paid_on + timedelta(days=90), self.legal))
        self.assertEqual(deed.events.first().note, "Created automatically")
        self.assertEqual(sale.events.get(action="payment").changes["fully_paid_on"], [None, paid_on.isoformat()])

    def test_a_payment_dated_before_the_sale_starts_the_documents_on_the_sale_date(self):
        sale = self.make_sale(price=1000, paid=0)
        services.record_payment(sale, self.manager, 1000, paid_on=self.sale_date - timedelta(days=3))
        self.assertEqual(self.fresh(sale).fully_paid_on, self.sale_date)

    def test_the_same_request_key_records_once_and_creates_obligations_once(self):
        sale = self.make_sale(price=1000, paid=400)
        first = services.record_payment(sale, self.manager, 600, idempotency_key="req-1")
        second = services.record_payment(sale, self.manager, 600, idempotency_key="req-1")
        self.assertFalse(first.duplicate)
        self.assertTrue(second.duplicate)
        self.assertEqual(second.payment.pk, first.payment.pk)
        self.assertEqual(second.obligations_created, [])
        self.assertEqual(sale.payments.filter(kind="payment").count(), 1)
        self.assertEqual(self.fresh(sale).amount_paid, Decimal("1000"))
        self.assertEqual(self.deeds(sale).count(), 1)
        self.assertEqual(self.ledger(sale), Decimal("1000"))

    def test_a_replayed_key_is_still_answered_after_the_sale_is_cancelled(self):
        sale = self.make_sale(price=1000, paid=0)
        first = services.record_payment(sale, self.manager, 100, idempotency_key="req-1")
        services.cancel_sale(sale, self.manager, "defaulted")
        again = services.record_payment(sale, self.manager, 100, idempotency_key="req-1")
        self.assertTrue(again.duplicate)
        self.assertEqual(again.payment.pk, first.payment.pk)

    def test_a_key_reused_for_a_different_amount_is_refused(self):
        sale = self.make_sale(price=1000, paid=0)
        services.record_payment(sale, self.manager, 100, idempotency_key="req-1")
        with self.assertRaises(ValueError):
            services.record_payment(sale, self.manager, 200, idempotency_key="req-1")
        self.assertEqual(sale.payments.count(), 1)

    def test_the_same_key_on_another_sale_is_a_different_request(self):
        one, two = self.make_sale(), self.make_sale()
        services.record_payment(one, self.manager, 100, idempotency_key="req-1")
        again = services.record_payment(two, self.manager, 100, idempotency_key="req-1")
        self.assertFalse(again.duplicate)

    def test_without_a_key_two_identical_payments_are_two_payments(self):
        sale = self.make_sale(price=1000, paid=0)
        services.record_payment(sale, self.manager, 100)
        services.record_payment(sale, self.manager, 100)
        self.assertEqual(sale.payments.count(), 2)
        self.assertEqual(self.fresh(sale).amount_paid, Decimal("200"))

    def test_a_payment_above_the_price_is_refused_and_nothing_is_written(self):
        sale = self.make_sale(price=1000, paid=900)
        with self.assertRaises(ValueError) as ctx:
            services.record_payment(sale, self.manager, 200)
        self.assertIn("100.00", str(ctx.exception))
        self.assertEqual(self.fresh(sale).amount_paid, Decimal("900"))
        self.assertEqual(sale.payments.count(), 1)
        services.record_payment(sale, self.manager, 100)                   # exactly what is left is fine
        self.assertEqual(self.fresh(sale).amount_paid, Decimal("1000"))

    def test_a_fully_paid_sale_accepts_no_more_money(self):
        sale = self.make_sale(price=1000, paid=1000)
        with self.assertRaises(ValueError):
            services.record_payment(sale, self.manager, 1)

    def test_bad_input_is_refused_and_nothing_is_written(self):
        sale = self.make_sale(price=1000, paid=100)
        for amount in (0, -5, "abc", "10.505", "NaN", "Infinity", None, "9" * 20):
            with self.subTest(amount=amount), self.assertRaises(ValueError):
                services.record_payment(sale, self.manager, amount)
        for day in ("31/02/2026", "1980-01-01", self.today + timedelta(days=10)):
            with self.subTest(day=day), self.assertRaises(ValueError):
                services.record_payment(sale, self.manager, 10, paid_on=day)
        with self.assertRaises(ValueError):
            services.record_payment(sale, self.manager, 10, idempotency_key="k" * 65)
        with self.assertRaises(ValueError):
            services.record_payment(sale, self.manager, 10, reference="r" * 121)
        self.assertEqual(sale.payments.count(), 1)
        self.assertEqual(self.fresh(sale).amount_paid, Decimal("100"))

    def test_a_cancelled_sale_takes_no_payments_and_a_reinstated_one_does(self):
        sale = self.make_sale(price=1000, paid=100)
        services.cancel_sale(sale, self.manager, "defaulted")
        with self.assertRaises(services.SaleStateError):
            services.record_payment(sale, self.manager, 100)
        self.assertEqual(sale.payments.count(), 1)
        services.reinstate_sale(sale, self.manager, "mistake")
        result = services.record_payment(sale, self.manager, 900)
        self.assertEqual([o.obligation_type.name for o in result.obligations_created], ["Deed"])

    def test_another_companys_user_cannot_record_a_payment(self):
        sale = self.make_sale()
        with self.assertRaises(PermissionDenied):
            services.record_payment(sale, self.eve, 10)
        self.assertEqual(sale.payments.count(), 0)

    def test_the_payment_and_the_obligations_are_one_unit(self):
        sale = self.make_sale(price=1000, paid=400)
        with patch("tracker.services.generate_obligations", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                services.record_payment(sale, self.manager, 600)
        fresh = self.fresh(sale)
        self.assertEqual((fresh.amount_paid, fresh.fully_paid_on), (Decimal("400"), None))
        self.assertEqual(sale.payments.count(), 1)
        self.assertEqual(sale.events.filter(action="payment").count(), 0)

    def test_the_ledger_always_adds_up_to_the_stored_total(self):
        sale = self.make_sale(price=1000, paid=400)
        services.record_payment(sale, self.manager, 100)
        second = services.record_payment(sale, self.manager, 200)
        services.reverse_payment(second.payment, self.manager, "Bounced cheque")
        services.register_payment_total(sale, 900)
        self.assertEqual(self.ledger(sale), self.fresh(sale).amount_paid)
        self.assertEqual(self.fresh(sale).amount_paid, Decimal("900"))


class ReversalTests(LedgerBase):
    def test_a_reversal_is_a_new_negative_entry_that_keeps_its_reason(self):
        sale = self.make_sale(price=1000, paid=400)
        paid = services.record_payment(sale, self.manager, 300).payment
        reversal = services.reverse_payment(paid, self.manager, "Cheque bounced")
        self.assertEqual((reversal.kind, reversal.amount, reversal.note, reversal.reverses, reversal.recorded_by),
                         ("reversal", Decimal("-300"), "Cheque bounced", paid, self.manager))
        paid.refresh_from_db()
        self.assertEqual((paid.kind, paid.amount), ("payment", Decimal("300")))      # the original is untouched
        self.assertEqual(self.fresh(sale).amount_paid, Decimal("400"))
        self.assertEqual(self.ledger(sale), Decimal("400"))
        event = sale.events.get(action="payment_reversed")
        self.assertEqual((event.note, event.actor, event.changes["amount_paid"]),
                         ("Cheque bounced", self.manager, ["700.00", "400.00"]))

    def test_a_reason_is_required(self):
        sale = self.make_sale(price=1000, paid=0)
        paid = services.record_payment(sale, self.manager, 100).payment
        for reason in (None, "", "   ", "x"):
            with self.subTest(reason=reason), self.assertRaises(ValueError):
                services.reverse_payment(paid, self.manager, reason)
        self.assertEqual(sale.payments.count(), 1)

    def test_a_payment_can_be_reversed_only_once_and_a_reversal_cannot_be_reversed(self):
        sale = self.make_sale(price=1000, paid=0)
        paid = services.record_payment(sale, self.manager, 100).payment
        reversal = services.reverse_payment(paid, self.manager, "Entered twice")
        with self.assertRaises(services.SaleStateError):
            services.reverse_payment(paid, self.manager, "again")
        with self.assertRaises(ValueError):
            services.reverse_payment(reversal, self.manager, "undo the undo")
        self.assertEqual(self.fresh(sale).amount_paid, Decimal("0"))

    def test_a_reversal_cannot_take_the_total_below_zero(self):
        sale = self.make_sale(price=1000, paid=0)
        paid = services.record_payment(sale, self.manager, 500).payment
        services.register_payment_total(sale, 100)
        with self.assertRaises(ValueError):
            services.reverse_payment(paid, self.manager, "wrong")
        self.assertEqual(self.fresh(sale).amount_paid, Decimal("100"))

    def test_a_cancelled_sale_cannot_have_payments_reversed(self):
        sale = self.make_sale(price=1000, paid=0)
        paid = services.record_payment(sale, self.manager, 100).payment
        services.cancel_sale(sale, self.manager, "defaulted")
        with self.assertRaises(services.SaleStateError):
            services.reverse_payment(paid, self.manager, "wrong")

    def test_an_opening_balance_can_be_reversed(self):
        sale = self.make_sale(price=1000, paid=400)
        services.reverse_payment(sale.payments.get(), self.manager, "Imported with the wrong amount")
        self.assertEqual(self.fresh(sale).amount_paid, Decimal("0"))
        self.assertEqual(self.ledger(sale), Decimal("0"))

    def test_reversing_the_completing_payment_keeps_existing_obligations_and_never_duplicates_them(self):
        sale = self.make_sale(price=1000, paid=0)
        full = services.record_payment(sale, self.manager, 1000)
        services.reverse_payment(full.payment, self.manager, "Transfer recalled")
        fresh = self.fresh(sale)
        self.assertEqual(fresh.amount_paid, Decimal("0"))
        self.assertFalse(fresh.is_fully_paid)
        self.assertIsNotNone(fresh.fully_paid_on)          # documented: the date of first completion is kept
        self.assertEqual(self.deeds(sale).count(), 1)      # existing work is never deleted
        again = services.record_payment(sale, self.manager, 1000)
        self.assertEqual(again.obligations_created, [])
        self.assertEqual(self.deeds(sale).count(), 1)

    def test_another_companys_user_cannot_reverse(self):
        sale = self.make_sale(price=1000, paid=0)
        paid = services.record_payment(sale, self.manager, 100).payment
        with self.assertRaises(PermissionDenied):
            services.reverse_payment(paid, self.eve, "not mine")


class AddSaleTests(LedgerBase):
    def add(self, **kw):
        values = dict(plot_number="X-1", estate=self.estate, price="1000", sale_date=self.today - timedelta(days=1),
                      buyer_name="Ada Obi")
        values.update(kw)
        return services.add_sale(self.co, self.manager, **values)

    def counts(self):
        return (Estate.objects.count(), Plot.objects.count(), Buyer.objects.count(), Sale.objects.count(),
                Payment.objects.count(), SaleEvent.objects.count(), Obligation.objects.count())

    def test_creates_the_estate_plot_buyer_and_sale_with_the_import_rules(self):
        sale = self.add(estate=None, estate_name="  Royal   Gardens ", plot_number=" R-1 ", price="1,500,000",
                        amount_paid="500000", phone="0803 111 2222", email="ada@example.com")
        self.assertEqual((sale.plot.estate.name, sale.plot.plot_number), ("Royal Gardens", "R-1"))
        self.assertEqual((sale.buyer.full_name, sale.buyer.phone, sale.buyer.email),
                         ("Ada Obi", "0803 111 2222", "ada@example.com"))
        self.assertEqual((sale.price, sale.amount_paid), (Decimal("1500000"), Decimal("500000")))
        opening = sale.payments.get()
        self.assertEqual((opening.kind, opening.recorded_by), ("opening", self.manager))
        self.assertEqual(set(sale.obligations.values_list("obligation_type__name", flat=True)), {"Allocation Letter"})
        self.assertEqual(sale.events.get().actor, self.manager)

    def test_a_fully_paid_sale_gets_the_deed_obligation(self):
        sale = self.add(amount_paid="1000")
        self.assertEqual(self.deeds(sale).count(), 1)
        self.assertEqual(sale.fully_paid_on, sale.sale_date)

    def test_existing_estates_and_plots_are_matched_ignoring_case_and_spacing(self):
        existing = Plot.objects.create(company=self.co, estate=self.estate, plot_number="P-9")
        before = (Estate.objects.count(), Plot.objects.count())
        sale = self.add(estate=None, estate_name=" green  VALLEY ", plot_number="p-9")
        self.assertEqual((sale.plot.pk, sale.plot.estate.pk), (existing.pk, self.estate.pk))
        self.assertEqual((Estate.objects.count(), Plot.objects.count()), before)

    def test_a_plot_with_an_active_sale_is_refused_and_names_the_buyer_and_leaves_nothing_behind(self):
        taken = self.make_sale()                                              # plot P-1, "Buyer 1"
        before = self.counts()
        with self.assertRaises(services.PlotOccupied) as ctx:
            self.add(plot_number="p-1", buyer_name="Someone Else")
        self.assertIn("Buyer 1", str(ctx.exception))
        self.assertEqual(ctx.exception.sale.pk, taken.pk)
        self.assertEqual(self.counts(), before)

    def test_a_cancelled_sale_frees_the_plot(self):
        taken = self.make_sale()
        services.cancel_sale(taken, self.manager, "defaulted")
        sale = self.add(plot_number="P-1", buyer_name="New Buyer")
        self.assertEqual(sale.plot.pk, taken.plot.pk)
        self.assertEqual(Sale.objects.filter(plot=taken.plot, status="active").count(), 1)

    def test_an_existing_buyer_is_reused_and_same_names_are_never_merged(self):
        sale_one = self.make_sale()
        buyers = Buyer.objects.count()
        self.add(plot_number="X-1", buyer=sale_one.buyer, buyer_name="")
        self.assertEqual(Buyer.objects.count(), buyers)
        self.add(plot_number="X-2", buyer_name=sale_one.buyer.full_name)       # same name typed in: a new person
        self.assertEqual(Buyer.objects.count(), buyers + 1)

    def test_bad_input_is_refused_and_nothing_is_written(self):
        before = self.counts()
        cases = {
            "price zero": dict(price="0"),
            "price text": dict(price="abc"),
            "paid more than price": dict(amount_paid="1500"),
            "negative paid": dict(amount_paid="-1"),
            "future date": dict(sale_date=self.today + timedelta(days=10)),
            "old date": dict(sale_date="1980-01-01"),
            "payment before the sale": dict(amount_paid="10", paid_on=self.today - timedelta(days=60)),
            "no plot": dict(plot_number=" "),
            "no estate": dict(estate=None, estate_name=""),
            "no buyer": dict(buyer_name="  "),
            "long buyer": dict(buyer_name="x" * 201),
            "bad email": dict(email="not-an-email"),
            "long phone": dict(phone="1" * 41),
        }
        for label, extra in cases.items():
            with self.subTest(label), self.assertRaises(ValueError):
                self.add(**extra)
        self.assertEqual(self.counts(), before)

    def test_another_companys_records_cannot_be_used(self):
        foreign_estate = Estate.objects.create(company=self.other, name="Foreign")
        foreign_buyer = Buyer.objects.create(company=self.other, full_name="Foreign Buyer")
        with self.assertRaises(PermissionDenied):
            self.add(estate=foreign_estate)
        with self.assertRaises(PermissionDenied):
            self.add(buyer=foreign_buyer)
        with self.assertRaises(PermissionDenied):
            services.add_sale(self.other, self.manager, plot_number="Z", estate=foreign_estate,
                              price="1", sale_date=self.today, buyer_name="x")


class BuyerEditTests(LedgerBase):
    def test_the_shared_buyer_is_updated_everywhere_without_merging_or_moving_sales(self):
        one = self.make_sale()
        buyer = one.buyer
        plot = Plot.objects.create(company=self.co, estate=self.estate, plot_number="Q-1")
        two = services.create_sale(self.co, plot, buyer, 500, self.sale_date)       # same person, second plot
        namesake = Buyer.objects.create(company=self.co, full_name=buyer.full_name)  # a different person, same name
        plot3 = Plot.objects.create(company=self.co, estate=self.estate, plot_number="Q-2")
        three = services.create_sale(self.co, plot3, namesake, 500, self.sale_date)
        buyers_before = Buyer.objects.count()

        services.edit_buyer(one, self.manager, full_name="Adaeze Obi", phone="0803 000 1111")

        buyer.refresh_from_db()
        namesake.refresh_from_db()
        self.assertEqual((buyer.full_name, buyer.phone), ("Adaeze Obi", "0803 000 1111"))
        self.assertNotEqual(namesake.full_name, "Adaeze Obi")                          # the namesake is untouched
        self.assertEqual(Buyer.objects.count(), buyers_before)
        self.assertEqual([self.fresh(s).buyer_id for s in (one, two, three)], [buyer.pk, buyer.pk, namesake.pk])
        edited = SaleEvent.objects.filter(action="buyer_edited")
        self.assertEqual({e.sale_id for e in edited}, {one.pk, two.pk})
        event = edited.get(sale=one)
        self.assertEqual((event.actor, event.changes["full_name"]), (self.manager, ["Buyer 1", "Adaeze Obi"]))
        self.assertFalse(three.events.filter(action="buyer_edited").exists())

    def test_only_the_fields_passed_change_and_email_can_be_cleared(self):
        sale = self.make_sale()
        Buyer.objects.filter(pk=sale.buyer_id).update(email="a@example.com", phone="0803")
        services.edit_buyer(sale, self.manager, email="")
        buyer = Buyer.objects.get(pk=sale.buyer_id)
        self.assertEqual((buyer.email, buyer.phone, buyer.full_name), ("", "0803", "Buyer 1"))

    def test_bad_input_and_no_change_are_refused(self):
        sale = self.make_sale()
        for extra in (dict(full_name=""), dict(full_name="x" * 201), dict(phone="1" * 41),
                      dict(email="nope"), dict(), dict(full_name="Buyer 1")):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                services.edit_buyer(sale, self.manager, **extra)
        self.assertFalse(sale.events.filter(action="buyer_edited").exists())

    def test_another_companys_user_cannot_edit(self):
        sale = self.make_sale()
        with self.assertRaises(PermissionDenied):
            services.edit_buyer(sale, self.eve, full_name="Hijacked")
        self.assertEqual(Buyer.objects.get(pk=sale.buyer_id).full_name, "Buyer 1")

    def test_a_cancelled_sales_buyer_can_still_be_corrected(self):
        sale = self.make_sale()
        services.cancel_sale(sale, self.manager, "defaulted")
        services.edit_buyer(sale, self.manager, phone="0803 222 3333")
        self.assertEqual(Buyer.objects.get(pk=sale.buyer_id).phone, "0803 222 3333")


class CancelReinstateHistoryTests(LedgerBase):
    def test_cancel_and_reinstate_are_written_to_the_sale_history(self):
        sale = self.make_sale()
        services.cancel_sale(sale, self.manager, "Buyer defaulted")
        services.reinstate_sale(sale, self.manager, "Cancelled by mistake")
        cancelled, reinstated = list(sale.events.filter(action__in=["cancelled", "reinstated"]))
        self.assertEqual((cancelled.actor, cancelled.note, cancelled.changes), (self.manager, "Buyer defaulted", {"status": ["active", "cancelled"]}))
        self.assertEqual((reinstated.actor, reinstated.note, reinstated.changes), (self.manager, "Cancelled by mistake", {"status": ["cancelled", "active"]}))

    def test_reinstating_creates_missing_obligations_and_reports_them_once(self):
        sale = self.make_sale(price=1000, paid=1000, paid_on=self.today - timedelta(days=10))
        self.deeds(sale).delete()
        services.cancel_sale(sale, self.manager, "defaulted")
        result = services.reinstate_sale(sale, self.manager, "mistake")
        self.assertEqual([o.obligation_type.name for o in result.obligations_created], ["Deed"])
        self.assertEqual(self.deeds(sale).count(), 1)

    def test_reinstating_a_complete_sale_creates_nothing_new(self):
        sale = self.make_sale(price=1000, paid=1000)
        before = Obligation.objects.count()
        services.cancel_sale(sale, self.manager, "defaulted")
        result = services.reinstate_sale(sale, self.manager)
        self.assertEqual(result.obligations_created, [])
        self.assertEqual(Obligation.objects.count(), before)

    def test_every_action_is_in_the_history_in_order_with_its_actor(self):
        sale = services.add_sale(self.co, self.manager, plot_number="Z-1", estate=self.estate, price="1000",
                                 sale_date=self.today - timedelta(days=2), buyer_name="Ada Obi")
        paid = services.record_payment(sale, self.manager, 500, reference="TRF-1").payment
        services.reverse_payment(paid, self.manager, "Wrong sale")
        services.edit_buyer(sale, self.manager, phone="08031112222")
        services.cancel_sale(sale, self.manager, "Buyer withdrew")
        services.reinstate_sale(sale, self.manager, "Mistake")
        self.assertEqual(list(sale.events.values_list("action", flat=True)),
                         ["created", "payment", "payment_reversed", "buyer_edited", "cancelled", "reinstated"])
        self.assertEqual(set(sale.events.values_list("actor", flat=True)), {self.manager.pk})
        self.assertEqual(list(sale.payments.values_list("kind", "amount")),
                         [("payment", Decimal("500")), ("reversal", Decimal("-500"))])


class ImportInteractionTests(TestCase):
    def committed(self, paid=500):
        co, mgr = make_company_with_types("C Homes")
        row = ["Green Valley", "A-1", "Ada Obi", 1000, paid, D(2026, 2, 3), None, None, None, None]
        batch = make_batch(co, mgr, [row])
        batch.report = validate_import(batch)[0]
        batch.status = "validated"
        batch.save()
        commit_batch(batch, mgr)
        batch.refresh_from_db()
        return co, mgr, batch, Sale.objects.get(company=co)

    def test_imported_sales_get_an_opening_balance_that_matches_the_sheet(self):
        co, mgr, batch, sale = self.committed(paid=500)
        row = sale.payments.get()
        self.assertEqual((row.kind, row.amount, row.recorded_by), ("opening", Decimal("500"), mgr))
        self.assertEqual(services.ledger_total(sale), sale.amount_paid)

    def test_a_clean_import_can_still_be_undone_and_everything_goes_with_it(self):
        co, mgr, batch, sale = self.committed()
        self.assertEqual(undo_blockers(batch), [])
        undo_batch(batch, mgr)
        self.assertEqual((Sale.objects.count(), Payment.objects.count(), SaleEvent.objects.count()), (0, 0, 0))

    def test_undo_is_blocked_after_a_payment(self):
        co, mgr, batch, sale = self.committed()
        services.record_payment(sale, mgr, 100)
        with self.assertRaises(UndoBlocked) as ctx:
            undo_batch(batch, mgr)
        self.assertTrue(any("payments" in r for r in ctx.exception.reasons))
        self.assertEqual((Sale.objects.count(), Payment.objects.count()), (1, 2))

    def test_undo_is_blocked_after_a_legacy_total_change(self):
        co, mgr, batch, sale = self.committed()
        services.register_payment_total(sale, 900)
        with self.assertRaises(UndoBlocked):
            undo_batch(batch, mgr)

    def test_undo_is_blocked_after_a_buyer_edit(self):
        co, mgr, batch, sale = self.committed()
        services.edit_buyer(sale, mgr, phone="0803 111 2222")
        with self.assertRaises(UndoBlocked) as ctx:
            undo_batch(batch, mgr)
        self.assertTrue(any("edited, cancelled or reinstated" in r for r in ctx.exception.reasons))

    def test_undo_is_blocked_after_a_cancellation(self):
        co, mgr, batch, sale = self.committed()
        services.cancel_sale(sale, mgr, "defaulted")
        with self.assertRaises(UndoBlocked) as ctx:
            undo_batch(batch, mgr)
        self.assertTrue(any("edited, cancelled or reinstated" in r for r in ctx.exception.reasons))


class DeletionAndAdminTests(LedgerBase):
    def test_deleting_a_sale_removes_its_ledger_and_history_even_with_reversals(self):
        sale = self.make_sale(price=1000, paid=0)
        paid = services.record_payment(sale, self.manager, 100).payment
        services.reverse_payment(paid, self.manager, "Entered twice")
        Sale.objects.filter(pk=sale.pk).delete()
        self.assertEqual((Payment.objects.count(), SaleEvent.objects.count()), (0, 0))

    def test_a_payment_that_has_a_reversal_cannot_be_deleted_on_its_own(self):
        sale = self.make_sale(price=1000, paid=0)
        paid = services.record_payment(sale, self.manager, 100).payment
        services.reverse_payment(paid, self.manager, "Entered twice")
        with self.assertRaises(RestrictedError):
            Payment.objects.get(pk=paid.pk).delete()
        self.assertEqual(sale.payments.count(), 2)

    def test_the_ledger_and_history_are_read_only_in_the_admin(self):
        root = User.objects.create_superuser("root", password="x")
        request = RequestFactory().get("/admin/")
        request.user = root
        for model in (Payment, SaleEvent):
            model_admin = admin.site._registry[model]
            self.assertFalse(model_admin.has_add_permission(request), model)
            self.assertFalse(model_admin.has_change_permission(request), model)
            self.assertFalse(model_admin.has_delete_permission(request), model)


class LedgerConstraintTests(LedgerBase):
    def entry(self, sale, **kw):
        values = dict(company=self.co, sale=sale, kind="payment", amount=Decimal("5"), paid_on=self.today)
        values.update(kw)
        return Payment.objects.create(**values)

    def test_the_database_refuses_impossible_entries(self):
        sale = self.make_sale()
        for label, extra in {
            "zero": dict(kind="adjustment", amount=Decimal("0")),
            "negative payment": dict(kind="payment", amount=Decimal("-5")),
            "positive reversal": dict(kind="reversal", amount=Decimal("5")),
            "negative opening": dict(kind="opening", amount=Decimal("-5")),
        }.items():
            with self.subTest(label), self.assertRaises(IntegrityError), transaction.atomic():
                self.entry(sale, **extra)

    def test_a_request_key_is_unique_per_sale_but_blank_keys_repeat(self):
        one, two = self.make_sale(), self.make_sale()
        self.entry(one, idempotency_key="abc")
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.entry(one, idempotency_key="abc")
        self.entry(two, idempotency_key="abc")
        self.entry(one)
        self.entry(one)


@skipUnless(connection.vendor == "postgresql", "needs PostgreSQL row locks")
class ConcurrentPaymentTests(TransactionTestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.co = Company.objects.create(name="A Homes")
        self.manager = User.objects.create_user("mike", password="x", company=self.co, role="manager")
        ObligationType.objects.create(company=self.co, name="Deed", trigger="on_full_payment", sla_days=90)
        estate = Estate.objects.create(company=self.co, name="Green Valley")
        plot = Plot.objects.create(company=self.co, estate=estate, plot_number="P-1")
        buyer = Buyer.objects.create(company=self.co, full_name="Buyer 1")
        self.sale = services.create_sale(self.co, plot, buyer, 1000, self.today - timedelta(days=30))

    def run_together(self, jobs):
        barrier = threading.Barrier(len(jobs))
        results, errors = [], []

        def worker(job):
            try:
                barrier.wait(timeout=15)
                results.append(job())
            except Exception as exc:                       # noqa: BLE001 - the tests inspect every error
                errors.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=worker, args=(job,)) for job in jobs]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        return results, errors

    def test_the_same_request_arriving_twice_at_once_is_recorded_once(self):
        job = lambda: services.record_payment(self.sale, self.manager, Decimal("1000"), idempotency_key="same-key")
        results, errors = self.run_together([job, job])
        self.assertEqual(errors, [])
        self.assertEqual(sorted(r.duplicate for r in results), [False, True])
        self.assertEqual(Payment.objects.filter(sale=self.sale, kind="payment").count(), 1)
        self.assertEqual(Sale.objects.get(pk=self.sale.pk).amount_paid, Decimal("1000"))
        self.assertEqual(Obligation.objects.filter(sale=self.sale, obligation_type__name="Deed").count(), 1)

    def test_two_different_payments_that_together_exceed_the_price_cannot_both_succeed(self):
        job = lambda: services.record_payment(self.sale, self.manager, Decimal("600"))
        results, errors = self.run_together([job, job])
        self.assertEqual((len(results), len(errors)), (1, 1))
        self.assertIsInstance(errors[0], ValueError)
        self.assertEqual(Sale.objects.get(pk=self.sale.pk).amount_paid, Decimal("600"))
        self.assertEqual(Payment.objects.filter(sale=self.sale).count(), 1)
