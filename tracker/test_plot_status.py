"""tracker/plot_status.py: availability derived from sales, and kept inside the plot's own company."""
from datetime import date

from django.test import TestCase

from tracker import inventory_services as inv
from tracker import plot_status, services
from tracker.models import Buyer, Company, Estate, Plot, Sale, User


class PlotStatusTests(TestCase):
    def setUp(self):
        self.a = Company.objects.create(name="A Homes")
        self.b = Company.objects.create(name="B Homes")
        self.md = User.objects.create_user("md_a", password="x", company=self.a, role="md")
        self.md_b = User.objects.create_user("md_b", password="x", company=self.b, role="md")
        self.estate = inv.create_estate(self.a, self.md, name="Green Valley")
        self.buyer = Buyer.objects.create(company=self.a, full_name="Ada Obi")

    def plot(self, number):
        return inv.register_plot(self.a, self.md, estate=self.estate, plot_number=number)

    def sell(self, plot, price=1000, paid=0):
        return services.create_sale(self.a, plot, self.buyer, price, date(2026, 1, 5), paid, actor=self.md)

    def board(self):
        return {p.plot_number: (plot_status.status_of(p), plot_status.payment_state_of(p), p.active_sale_id)
                for p in plot_status.plots_with_status(self.estate)}

    def test_every_state_of_a_plot(self):
        free = self.plot("A-1")
        part = self.plot("A-2")
        full = self.plot("A-3")
        reversed_ = self.plot("A-4")
        cancelled = self.plot("A-5")
        resold = self.plot("A-6")
        reinstated = self.plot("A-7")

        s_part = self.sell(part, paid=400)
        s_full = self.sell(full, paid=1000)
        s_rev = self.sell(reversed_, paid=0)
        paid = services.record_payment(s_rev, self.md, 1000, idempotency_key="k1")
        services.reverse_payment(paid.payment, self.md, "cheque bounced")
        s_can = self.sell(cancelled)
        services.cancel_sale(s_can, self.md, "buyer withdrew")
        old = self.sell(resold)
        services.cancel_sale(old, self.md, "buyer withdrew")
        s_new = self.sell(resold, paid=1000)
        s_re = self.sell(reinstated)
        services.cancel_sale(s_re, self.md, "entered by mistake")
        services.reinstate_sale(s_re, self.md, "it was valid")

        board = self.board()
        self.assertEqual(board["A-1"], ("available", None, None))
        self.assertEqual(board["A-2"], ("sold", "part_paid", s_part.pk))
        self.assertEqual(board["A-3"], ("sold", "fully_paid", s_full.pk))
        self.assertEqual(board["A-5"], ("available", None, None))                     # a cancelled sale frees the plot
        self.assertEqual(board["A-6"], ("sold", "fully_paid", s_new.pk))              # the resale, not the cancelled one
        self.assertEqual(board["A-7"], ("sold", "part_paid", s_re.pk))                # reinstated: active again
        # Paid in full, then the payment was reversed: the stored amount is the truth, not fully_paid_on.
        s_rev.refresh_from_db()
        self.assertIsNotNone(s_rev.fully_paid_on)
        self.assertEqual(board["A-4"], ("sold", "part_paid", s_rev.pk))
        self.assertEqual(free.pk, [p.pk for p in plot_status.plots_with_status(self.estate)][0])

    def test_it_agrees_with_the_sales_rule_for_every_plot(self):
        """The board's idea of 'who holds this plot' is the same as services._active_sale_on, state by state."""
        plots = [self.plot(f"P-{n}") for n in range(6)]
        self.sell(plots[1])
        services.cancel_sale(self.sell(plots[2]), self.md, "buyer withdrew")
        services.cancel_sale(self.sell(plots[3]), self.md, "buyer withdrew")
        self.sell(plots[3])
        for annotated in plot_status.plots_with_status(self.estate):
            current = services._active_sale_on(annotated)
            self.assertEqual(annotated.active_sale_id, current.pk if current else None, annotated.plot_number)

    def test_one_query_however_many_plots(self):
        Plot.objects.bulk_create([Plot(company=self.a, estate=self.estate, plot_number=f"B-{n}") for n in range(120)])
        for plot in Plot.objects.filter(estate=self.estate)[:40]:
            Sale.objects.create(company=self.a, plot=plot, buyer=self.buyer, price=1000, amount_paid=1000,
                                sale_date=date(2026, 1, 5))
        with self.assertNumQueries(1):
            rows = list(plot_status.plots_with_status(self.estate))
        self.assertEqual(len(rows), 120)
        self.assertEqual(sum(1 for p in rows if p.active_sale_id), 40)

    def test_the_order_is_stable_by_canonical_key_then_id(self):
        for number in ("b-2", "A-10", "A-1", "a-2"):
            self.plot(number)
        self.assertEqual([p.plot_number for p in plot_status.plots_with_status(self.estate)],
                         ["A-1", "A-10", "a-2", "b-2"])

    def test_a_sale_carrying_another_companys_id_never_shows_on_this_plot(self):
        plot = self.plot("A-1")
        buyer_b = Buyer.objects.create(company=self.b, full_name="Bayo Lawal")
        Sale.objects.create(company=self.b, plot=plot, buyer=buyer_b, price=1000, sale_date=date(2026, 1, 5))
        self.assertEqual(self.board()["A-1"], ("available", None, None))
        self.assertEqual(Sale.objects.filter(plot=plot, status="active").count(), 1)       # it really exists

    def test_a_plot_of_another_company_under_this_estate_is_not_listed(self):
        self.plot("A-1")
        Plot.objects.create(company=self.b, estate=self.estate, plot_number="X-1")         # bypassing the services
        self.assertEqual(sorted(self.board()), ["A-1"])

    def test_an_empty_estate_has_no_plots(self):
        self.assertEqual(list(plot_status.plots_with_status(self.estate)), [])

    def test_the_ceiling_has_a_default_and_can_be_configured(self):
        from django.test import override_settings
        self.assertEqual(plot_status.board_max_plots(), 5000)
        with override_settings(INVENTORY_BOARD_MAX_PLOTS=7):
            self.assertEqual(plot_status.board_max_plots(), 7)
