from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.utils import timezone

from importer.commit import commit_batch, undo_batch
from importer.errors import UndoBlocked
from importer.tests.helpers import D, make_batch, make_company_with_types
from importer.validation import validate_import

from . import reports, services
from .models import (
    Buyer, Company, Department, Estate, Obligation, ObligationEvent, ObligationType, Plot, Sale, User,
)


class Base(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.co = Company.objects.create(name="A Homes")
        self.other = Company.objects.create(name="B Homes")
        self.legal = Department.objects.create(company=self.co, name="Legal")
        self.manager = User.objects.create_user("mike", password="x", first_name="Mike", last_name="Obi", company=self.co, role="manager")
        self.sarah = User.objects.create_user("sarah", password="x", first_name="Sarah", company=self.co, role="staff", department=self.legal)
        self.eve = User.objects.create_user("eve", password="x", company=self.other, role="manager")
        self.alloc = ObligationType.objects.create(company=self.co, name="Allocation Letter", trigger="on_sale", sla_days=30)
        self.deed = ObligationType.objects.create(company=self.co, name="Deed", trigger="on_full_payment", sla_days=90,
                                                  default_department=self.legal)
        self.e1 = Estate.objects.create(company=self.co, name="Green Valley")
        self.e2 = Estate.objects.create(company=self.co, name="Royal Gardens")
        self.n = 0

    def sale(self, estate=None, price=1000, paid=1000, sold_ago=100, paid_ago=None):
        self.n += 1
        plot = Plot.objects.create(company=self.co, estate=estate or self.e1, plot_number=f"P-{self.n}")
        buyer = Buyer.objects.create(company=self.co, full_name=f"Buyer {self.n}")
        sold = self.today - timedelta(days=sold_ago)
        paid_on = self.today - timedelta(days=paid_ago if paid_ago is not None else sold_ago)
        return services.create_sale(self.co, plot, buyer, price, sold, paid, paid_on)

    def age(self, ob, days):
        Obligation.objects.filter(pk=ob.pk).update(last_updated_at=timezone.now() - timedelta(days=days))


class CancelledSaleRuleTests(Base):
    def setUp(self):
        super().setUp()
        self.keep = self.sale(price=500, paid=500, sold_ago=60)
        self.gone = self.sale(price=2000, paid=2000, sold_ago=200)            # old, overdue, will be cancelled
        self.gone_deed = self.gone.obligations.get(obligation_type=self.deed)
        services.update_obligation(self.gone_deed, self.sarah, owner=self.sarah, status="awaiting_external",
                                   reason="Registry backlog", next_action="Chase registry")
        self.age(self.gone_deed, 30)                                              # stale

    def figures(self):
        c = self.co
        return {
            "open": services.open_obligations(c).count(),
            "exposure": services.exposure(c)["amount"],
            "ageing": sum(services.ageing(c, self.today).values()),
            "overdue": services.overdue(c, self.today).count(),
            "stale": services.stale(c).count(),
            "queue": services.my_queue(self.sarah).count(),
            "ball": sum(r["open"] for r in services.who_has_the_ball(c, self.today)),
            "attention": reports.attention_summary(c, self.today)["open"],
            "by_estate": sum(r["amount"] for r in reports.by_estate(c, self.today)),
        }

    def test_cancelled_obligations_disappear_from_every_figure_but_stay_stored(self):
        before = self.figures()
        self.assertEqual(before["queue"], 1)
        snapshot = {o.pk: (o.status, o.owner_id, o.last_updated_at, o.reason) for o in self.gone.obligations.all()}
        services.cancel_sale(self.gone, self.manager, "Buyer defaulted")
        after = self.figures()
        for key in before:
            self.assertLess(after[key], before[key], key)                         # every single figure dropped
        self.assertEqual(after["queue"], 0)
        self.assertEqual(after["exposure"], Decimal("500.00"))                    # only the kept sale remains
        self.assertEqual(self.gone.obligations.count(), 2)                        # permanently stored
        self.assertEqual({o.pk: (o.status, o.owner_id, o.last_updated_at, o.reason) for o in self.gone.obligations.all()},
                         snapshot)                                                # state preserved exactly (status, owner, timestamps)

    def test_cancellation_is_recorded(self):
        before = ObligationEvent.objects.filter(obligation__sale=self.gone).count()
        sale = services.cancel_sale(self.gone, self.manager, "  Buyer defaulted  ")
        self.assertEqual((sale.status, sale.cancelled_by, sale.cancel_reason), ("cancelled", self.manager, "Buyer defaulted"))
        self.assertIsNotNone(sale.cancelled_at)
        events = ObligationEvent.objects.filter(obligation__sale=self.gone).order_by("-id")[:2]
        self.assertEqual(ObligationEvent.objects.filter(obligation__sale=self.gone).count(), before + 2)
        for ev in events:
            self.assertEqual((ev.note, ev.actor, ev.changes), ("Sale cancelled: Buyer defaulted", self.manager, {}))

    def test_validation_and_permissions(self):
        for bad in (None, "", "  ", "x"):
            with self.assertRaises(ValueError):
                services.cancel_sale(self.gone, self.manager, bad)
        with self.assertRaises(PermissionDenied):
            services.cancel_sale(self.gone, self.eve, "reason")
        services.cancel_sale(self.gone, self.manager, "reason")
        with self.assertRaises(services.SaleStateError):
            services.cancel_sale(self.gone, self.manager, "again")

    def test_obligations_of_a_cancelled_sale_are_read_only(self):
        services.cancel_sale(self.gone, self.manager, "reason")
        with self.assertRaises(services.SaleStateError):
            services.update_obligation(self.gone_deed, self.manager, note="trying to edit")
        self.gone_deed.refresh_from_db()
        self.assertEqual(self.gone_deed.status, "awaiting_external")

    def test_reinstating_restores_everything_as_it_was(self):
        before = self.figures()
        services.cancel_sale(self.gone, self.manager, "mistake")
        services.reinstate_sale(self.gone, self.manager, "cancelled by mistake")
        self.assertEqual(self.figures(), before)
        sale = Sale.objects.get(pk=self.gone.pk)
        self.assertEqual((sale.status, sale.cancelled_at, sale.cancel_reason), ("active", None, ""))
        self.assertTrue(ObligationEvent.objects.filter(obligation__sale=sale, note="Sale reinstated: cancelled by mistake").exists())
        services.update_obligation(self.gone_deed, self.manager, note="editable again")

    def test_reinstating_is_refused_when_the_plot_was_sold_again(self):
        services.cancel_sale(self.gone, self.manager, "defaulted")
        services.create_sale(self.co, self.gone.plot, Buyer.objects.create(company=self.co, full_name="New Buyer"), 900,
                             self.today, 900, self.today)
        with self.assertRaises(services.SaleStateError):
            services.reinstate_sale(self.gone, self.manager)
        with self.assertRaises(services.SaleStateError):                           # an active sale cannot be 'reinstated'
            services.reinstate_sale(self.keep, self.manager)

    def test_resale_creates_fresh_obligations_and_the_old_ones_stay_excluded(self):
        services.cancel_sale(self.gone, self.manager, "defaulted")
        resale = services.create_sale(self.co, self.gone.plot, Buyer.objects.create(company=self.co, full_name="New Buyer"),
                                      900, self.today, 900, self.today)
        self.assertEqual(resale.obligations.count(), 2)
        self.assertEqual(Obligation.objects.filter(sale__plot=self.gone.plot).count(), 4)
        self.assertEqual(services.exposure(self.co)["amount"], Decimal("1400.00"))      # 500 + 900, not the cancelled 2000

    def test_cancelled_sales_get_no_new_obligations(self):
        partial = self.sale(price=1000, paid=100, sold_ago=10)
        services.cancel_sale(partial, self.manager, "defaulted")
        services.register_payment_total(partial, 1000)
        self.assertFalse(partial.obligations.filter(obligation_type=self.deed).exists())

    def test_import_treats_a_cancelled_plot_as_free_and_cancellation_blocks_undo(self):
        co, mgr = make_company_with_types("C Homes")
        row = ["Green Valley", "A-1", "Ada Obi", 1000, 1000, D(2026, 2, 3), D(2026, 2, 10), None, None, None]
        first = make_batch(co, mgr, [row])
        first.report = validate_import(first)[0]
        first.status = "validated"
        first.save()
        commit_batch(first, mgr)
        sale = Sale.objects.get(company=co)
        services.cancel_sale(sale, mgr, "defaulted")
        with self.assertRaises(UndoBlocked):                                        # a later human action blocks the undo
            undo_batch(first, mgr)
        again = make_batch(co, mgr, [[*row[:2], "New Buyer", *row[3:]]], filename="resale.xlsx")
        report, prepared = validate_import(again)
        self.assertEqual((report["summary"]["to_import"], report["summary"]["conflicts"]), (1, 0))   # the plot is free again


class ReportTests(Base):
    def test_attention_counts(self):
        a = self.sale(sold_ago=100)                                                # alloc overdue, deed due in -10 -> overdue
        b = self.sale(sold_ago=5, paid=100, price=1000)                            # only alloc, due in 25 days
        c = self.sale(sold_ago=25, paid=1000, paid_ago=25)                         # alloc due in 5 days, deed due in 65
        services.update_obligation(a.obligations.get(obligation_type=self.deed), self.sarah, owner=self.sarah,
                                   status="awaiting_external", next_action="Chase")
        self.age(a.obligations.get(obligation_type=self.alloc), 20)
        got = reports.attention_summary(self.co, self.today)
        self.assertEqual(got["open"], 5)
        self.assertEqual(got["overdue"], 2)                                        # a.alloc, a.deed
        self.assertEqual(got["due_soon"], 1)                                       # c.alloc (5 days)
        self.assertEqual(got["stale"], 1)
        self.assertEqual(got["awaiting_external"], 1)
        self.assertEqual(got["incomplete"], 4)                                     # all except a.deed

    def test_exposure_by_estate_adds_up_to_total_and_counts_each_sale_once(self):
        self.sale(self.e1, price=1000, paid=1000)
        self.sale(self.e1, price=2000, paid=500, sold_ago=10)
        self.sale(self.e2, price=3000, paid=3000, sold_ago=40)
        rows = reports.by_estate(self.co, self.today)
        self.assertEqual({r["estate"]: r["amount"] for r in rows}, {"Green Valley": Decimal("1500.00"), "Royal Gardens": Decimal("3000.00")})
        self.assertEqual(sum(r["amount"] for r in rows), services.exposure(self.co)["amount"])
        self.assertEqual([r["estate"] for r in rows], ["Royal Gardens", "Green Valley"])      # biggest first
        gv = next(r for r in rows if r["estate"] == "Green Valley")
        self.assertEqual((gv["sales"], gv["oldest_days"]), (2, 100))

    def test_by_type_is_per_document_and_not_additive(self):
        self.sale(price=1000, paid=1000)
        t = {r["type"]: r for r in reports.by_type(self.co, self.today)}
        self.assertEqual((t["Deed"]["open"], t["Allocation Letter"]["open"]), (1, 1))
        self.assertEqual(t["Deed"]["amount"], Decimal("1000.00"))
        self.assertEqual(t["Allocation Letter"]["amount"], Decimal("1000.00"))               # same money, two documents

    def test_average_wait_and_empty_company(self):
        self.sale(sold_ago=10)
        self.sale(sold_ago=30)
        self.assertEqual(reports.average_wait_days(self.co, self.today), 20.0)
        self.assertIsNone(reports.average_wait_days(self.other, self.today))
        self.assertEqual(reports.attention_summary(self.other, self.today)["open"], 0)
        self.assertEqual(reports.by_estate(self.other, self.today), [])

    def test_delay_reasons_group_ignoring_case_and_ignore_closed_and_cancelled(self):
        sales = [self.sale() for _ in range(4)]
        deeds = [s.obligations.get(obligation_type=self.deed) for s in sales]
        for ob, text in zip(deeds, ["Registry backlog", " registry   BACKLOG ", "Missing buyer ID", "Registry backlog"]):
            services.update_obligation(ob, self.sarah, reason=text)
        services.update_obligation(deeds[3], self.sarah, status="completed")             # closed: not counted
        self.assertEqual(reports.delay_reasons(self.co), [{"reason": "Registry backlog", "count": 2},
                                                          {"reason": "Missing buyer ID", "count": 1}])
        services.cancel_sale(sales[2], self.manager, "defaulted")
        self.assertEqual(reports.delay_reasons(self.co), [{"reason": "Registry backlog", "count": 2}])

    def test_oldest_cases_order_limit_and_exclusion(self):
        old = self.sale(sold_ago=300)
        self.sale(sold_ago=20)
        cancelled = self.sale(sold_ago=500)
        services.cancel_sale(cancelled, self.manager, "defaulted")
        rows = list(reports.oldest_cases(self.co, 3))
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0].sale, old)
        self.assertEqual([r.started_on for r in rows], sorted(r.started_on for r in rows))
        self.assertNotIn(cancelled.pk, {r.sale_id for r in reports.oldest_cases(self.co, 50)})

    def test_workload_by_department_and_owner(self):
        a = self.sale(sold_ago=100)
        self.sale(sold_ago=100)
        for ob in a.obligations.all():
            services.update_obligation(ob, self.sarah, owner=self.sarah)
        w = reports.workload(self.co, self.today)
        by_dept = {r["department"]: r for r in w["by_department"]}
        self.assertEqual(by_dept["Legal"]["open"], 2)                                      # a.deed and the other deed (default dept Legal)
        self.assertEqual(by_dept["Unassigned"]["open"], 2)                                 # allocation letters have no department
        self.assertEqual(w["by_owner"][0]["owner"], "Sarah")
        self.assertEqual(w["by_owner"][0]["open"], 2)
        self.assertEqual(sum(r["unowned"] for r in w["by_department"]), 2)

    def test_queue_groups_partition(self):
        s = self.sale(sold_ago=100)                                                        # alloc: overdue; deed: overdue
        u = self.sale(sold_ago=25, paid_ago=25)                                            # alloc due in 5 days
        awaiting = u.obligations.get(obligation_type=self.deed)
        for ob in list(s.obligations.all()) + list(u.obligations.all()):
            services.update_obligation(ob, self.sarah, owner=self.sarah)
        services.update_obligation(awaiting, self.sarah, status="awaiting_external")
        groups = reports.queue_groups(self.sarah, self.today)
        self.assertEqual({k: len(v) for k, v in groups.items()}, {"overdue": 2, "due_soon": 1, "awaiting_external": 1, "other": 0})
        self.assertEqual(sum(len(v) for v in reports.queue_groups(self.manager, self.today).values()), 0)
