from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from . import services as s
from .importing import clean_amount, clean_date
from .models import Buyer, Company, Department, Estate, Obligation, ObligationType, Plot, Sale, User

TODAY = date(2026, 10, 3)


class BaseCase(TestCase):
    def setUp(self):
        self.co = Company.objects.create(name="A Homes")
        self.other = Company.objects.create(name="B Homes")
        self.legal = Department.objects.create(company=self.co, name="Legal")
        self.sarah = User.objects.create_user("sarah", password="x", company=self.co, department=self.legal)
        self.eve = User.objects.create_user("eve", password="x", company=self.other)
        self.estate = Estate.objects.create(company=self.co, name="Green Valley")
        self.deed = ObligationType.objects.create(
            company=self.co, name="Deed of Assignment", trigger="on_full_payment", sla_days=90, default_department=self.legal)
        self.alloc = ObligationType.objects.create(company=self.co, name="Allocation Letter", trigger="on_sale", sla_days=30)
        self.n = 0

    def sale(self, price=1000, paid=0, sale_date=None, paid_on=None):
        self.n += 1
        plot = Plot.objects.create(company=self.co, estate=self.estate, plot_number=f"A-{self.n}")
        buyer = Buyer.objects.create(company=self.co, full_name=f"Buyer {self.n}")
        return s.create_sale(self.co, plot, buyer, price, sale_date or TODAY, paid, paid_on)


class ObligationEngineTests(BaseCase):
    def test_generation_follows_triggers(self):
        sale = self.sale(price=1000, paid=400)
        self.assertEqual(set(sale.obligations.values_list("obligation_type__name", flat=True)), {"Allocation Letter"})
        s.register_payment_total(sale, 1000, paid_on=date(2026, 9, 1))
        deed = sale.obligations.get(obligation_type=self.deed)
        self.assertEqual(deed.started_on, date(2026, 9, 1))
        self.assertEqual(deed.due_date, date(2026, 9, 1) + timedelta(days=90))
        self.assertEqual(deed.department, self.legal)

    def test_generation_is_idempotent(self):
        sale = self.sale(price=1000, paid=1000)
        before = Obligation.objects.count()
        s.generate_obligations(sale)
        s.register_payment_total(sale, 1000)
        self.assertEqual(Obligation.objects.count(), before)

    def test_update_logs_before_after_and_refreshes_timestamp(self):
        sale = self.sale(paid=1000)
        ob = sale.obligations.get(obligation_type=self.deed)
        Obligation.objects.filter(pk=ob.pk).update(last_updated_at=timezone.now() - timedelta(days=30))
        s.update_obligation(ob, self.sarah, owner=self.sarah, status="awaiting_external",
                            reason="Awaiting solicitor", next_action="Follow up", note="Sent to solicitor")
        ob.refresh_from_db()
        ev = ob.events.last()
        self.assertEqual(ev.changes["status"], ["not_started", "awaiting_external"])
        self.assertEqual(ev.actor, self.sarah)
        self.assertGreater(ob.last_updated_at, timezone.now() - timedelta(minutes=1))

    def test_completion_sets_and_clears_completed_on(self):
        ob = self.sale(paid=1000).obligations.get(obligation_type=self.deed)
        s.update_obligation(ob, self.sarah, status="completed")
        ob.refresh_from_db(); self.assertIsNotNone(ob.completed_on)
        s.update_obligation(ob, self.sarah, status="in_progress")
        ob.refresh_from_db(); self.assertIsNone(ob.completed_on)

    def test_other_company_cannot_edit(self):
        ob = self.sale(paid=1000).obligations.first()
        with self.assertRaises(PermissionDenied):
            s.update_obligation(ob, self.eve, status="completed")

    def test_untracked_field_rejected(self):
        ob = self.sale(paid=1000).obligations.first()
        with self.assertRaises(ValueError):
            s.update_obligation(ob, self.sarah, started_on=TODAY)


class ReportingTests(BaseCase):
    def test_exposure_counts_each_sale_once(self):
        self.sale(price=1000, paid=1000)           # 2 open obligations, counted once
        self.sale(price=500, paid=200)             # partly paid, 1 open (allocation)
        self.assertEqual(s.exposure(self.co)["amount"], Decimal("1200"))
        self.assertEqual(s.exposure(self.co)["sales"], 2)
        self.assertEqual(s.exposure(self.co, fully_paid_only=True)["amount"], Decimal("1000"))

    def test_completed_sale_leaves_exposure(self):
        sale = self.sale(price=1000, paid=1000)
        for ob in sale.obligations.all():
            s.update_obligation(ob, self.sarah, status="completed")
        self.assertEqual(s.exposure(self.co)["amount"], Decimal("0"))

    def test_exposure_is_company_scoped(self):
        self.sale(price=1000, paid=1000)
        self.assertEqual(s.exposure(self.other)["amount"], Decimal("0"))

    def test_ageing_buckets(self):
        for days in (10, 45, 75, 120):
            self.sale(price=100, paid=100, sale_date=TODAY - timedelta(days=days), paid_on=TODAY - timedelta(days=days))
        # each sale has Deed + Allocation, both start on the same day -> 2 obligations per bucket
        self.assertEqual(s.ageing(self.co, today=TODAY), {"d0_30": 2, "d31_60": 2, "d61_90": 2, "d90_plus": 2})

    def test_overdue_and_stale(self):
        sale = self.sale(price=100, paid=100, sale_date=TODAY - timedelta(days=100), paid_on=TODAY - timedelta(days=100))
        self.assertEqual(s.overdue(self.co, today=TODAY).count(), 2)   # 90-day deed and 30-day allocation
        Obligation.objects.filter(sale=sale).update(last_updated_at=timezone.now() - timedelta(days=20))
        self.assertEqual(s.stale(self.co, days=14).count(), 2)

    def test_queue_and_ball(self):
        ob = self.sale(paid=1000).obligations.get(obligation_type=self.deed)
        s.update_obligation(ob, self.sarah, owner=self.sarah)
        self.assertEqual(list(s.my_queue(self.sarah)), [ob])
        self.assertEqual(s.my_queue(self.eve).count(), 0)
        self.assertTrue(any(r["owner__username"] == "sarah" for r in s.who_has_the_ball(self.co)))


class IntegrityTests(BaseCase):
    def test_one_active_sale_per_plot_but_resale_after_cancel(self):
        sale = self.sale()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Sale.objects.create(company=self.co, plot=sale.plot, buyer=sale.buyer, price=1, sale_date=TODAY)
        sale.status = "cancelled"; sale.save()
        Sale.objects.create(company=self.co, plot=sale.plot, buyer=sale.buyer, price=1, sale_date=TODAY)


class ImportHelperTests(SimpleTestCase):
    def test_amounts(self):
        for raw, want in [("₦25,000,000", 25000000), ("N25,000,000.50", Decimal("25000000.50")), (" 1 500 000 ", 1500000), (2500, 2500)]:
            self.assertEqual(clean_amount(raw), Decimal(str(want)))
        for bad in ("", None, "TBC", "25m"):
            with self.assertRaises(ValueError):
                clean_amount(bad)

    def test_dates_are_day_first(self):
        self.assertEqual(clean_date("03/10/2026"), date(2026, 10, 3))
        self.assertEqual(clean_date("3 Oct 2026"), date(2026, 10, 3))
        self.assertEqual(clean_date("2026-10-03"), date(2026, 10, 3))
        for bad in ("", None, "soon", "31/02/2026"):
            with self.assertRaises(ValueError):
                clean_date(bad)
