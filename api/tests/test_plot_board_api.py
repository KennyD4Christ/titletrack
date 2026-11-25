"""GET /api/plot-board/?estate=<id>: what every role sees, what only view_sales roles see, and company isolation."""
from datetime import date
import json

from django.test import override_settings
from django.urls import reverse

from api.permissions import ALL_ROLES, can
from api.tests.inventory_fixtures import InventoryApiBase
from tracker import services
from tracker.models import Buyer, Plot, Sale

PUBLIC_KEYS = {"id", "plot_number", "status"}
SALES_KEYS = PUBLIC_KEYS | {"payment_state", "active_sale_id"}
# Anything that would identify a buyer or reveal money or dates. None may ever appear in a board response.
FORBIDDEN = {"buyer", "buyer_name", "buyer_phone", "phone", "email", "full_name", "price", "amount_paid",
             "balance_due", "is_fully_paid", "sale_date", "fully_paid_on", "paid_on", "cancel_reason", "cancelled_at",
             "obligations", "payments", "history"}


def all_keys(value):
    """Every dictionary key anywhere in a JSON value."""
    if isinstance(value, dict):
        for key, inner in value.items():
            yield key
            yield from all_keys(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from all_keys(inner)


def all_values(value):
    """Every scalar value anywhere in a JSON value."""
    if isinstance(value, dict):
        for inner in value.values():
            yield from all_values(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from all_values(inner)
    else:
        yield value


class BoardBase(InventoryApiBase):
    def url(self, estate=None):
        return f"{reverse('plot-board')}?estate={(estate or self.estate).pk}"

    def get(self, role="md", estate=None):
        self.login(role)
        return self.client.get(self.url(estate))

    def mixed_estate(self):
        self.p = {name: self.plot(name) for name in ("A-1", "A-2", "A-3", "A-4")}
        self.s_part = self.sell(self.p["A-2"], paid=400)
        self.s_full = self.sell(self.p["A-3"], paid=1000)
        cancelled = self.sell(self.p["A-4"])
        services.cancel_sale(cancelled, self.md, "buyer withdrew")


class PlotBoardContentTests(BoardBase):
    def test_a_sales_capable_role_sees_status_payment_state_and_the_sale_to_open(self):
        self.mixed_estate()
        body = self.get("md").data
        rows = {r["plot_number"]: r for r in body["plots"]}
        self.assertEqual(rows["A-1"], {"id": self.p["A-1"].pk, "plot_number": "A-1", "status": "available",
                                       "payment_state": None, "active_sale_id": None})
        self.assertEqual((rows["A-2"]["status"], rows["A-2"]["payment_state"], rows["A-2"]["active_sale_id"]),
                         ("sold", "part_paid", self.s_part.pk))
        self.assertEqual((rows["A-3"]["status"], rows["A-3"]["payment_state"], rows["A-3"]["active_sale_id"]),
                         ("sold", "fully_paid", self.s_full.pk))
        self.assertEqual(rows["A-4"]["status"], "available")                          # cancelled sale: free again

    def test_the_envelope(self):
        self.mixed_estate()
        body = self.get("md").data
        self.assertEqual(set(body), {"estate", "as_of", "count", "truncated", "plots"})
        self.assertEqual(body["estate"], {"id": self.estate.pk, "name": "Green Valley", "location": "Lekki"})
        self.assertEqual((body["count"], body["truncated"], len(body["plots"])), (4, False, 4))
        self.assertIsInstance(body["as_of"], str)
        self.assertEqual([r["plot_number"] for r in body["plots"]], ["A-1", "A-2", "A-3", "A-4"])

    def test_an_estate_with_no_plots_is_an_empty_board_not_an_error(self):
        body = self.get("md").data
        self.assertEqual((body["count"], body["plots"], body["truncated"]), (0, [], False))

    def test_the_response_is_not_cacheable_by_a_shared_cache(self):
        response = self.get("viewer")
        self.assertIn("private", response["Cache-Control"])
        self.assertIn("no-cache", response["Cache-Control"])

    def test_a_cancelled_and_resold_plot_shows_the_resale(self):
        plot = self.plot("R-1")
        old = self.sell(plot)
        services.cancel_sale(old, self.md, "buyer withdrew")
        new = self.sell(plot, buyer=Buyer.objects.create(company=self.a, full_name="Bayo Lawal"))
        row = self.get("md").data["plots"][0]
        self.assertEqual((row["status"], row["active_sale_id"]), ("sold", new.pk))


class PlotBoardVisibilityTests(BoardBase):
    def test_every_role_can_read_the_board_and_sees_exactly_the_keys_its_permission_allows(self):
        self.mixed_estate()
        for role in sorted(ALL_ROLES):
            response = self.get(role)
            self.assertEqual(response.status_code, 200, role)
            expected = SALES_KEYS if can(self.users[role], "view_sales") else PUBLIC_KEYS
            for row in response.data["plots"]:
                self.assertEqual(set(row), expected, f"{role}: {row}")
        self.assertEqual({r for r in ALL_ROLES if can(self.users[r], "view_sales")}, {"md", "manager"})

    def test_roles_without_view_sales_see_sold_or_available_and_nothing_about_the_sale(self):
        self.mixed_estate()
        for role in ("staff", "sales", "viewer"):
            rows = {r["plot_number"]: r for r in self.get(role).data["plots"]}
            self.assertEqual({k: v["status"] for k, v in rows.items()},
                             {"A-1": "available", "A-2": "sold", "A-3": "sold", "A-4": "available"})
            for row in rows.values():
                self.assertNotIn("active_sale_id", row)
                self.assertNotIn("payment_state", row)

    def test_no_role_ever_sees_a_buyer_a_price_an_amount_or_a_date(self):
        self.mixed_estate()
        for role in sorted(ALL_ROLES):
            leaked = FORBIDDEN & set(all_keys(self.get(role).data))
            self.assertEqual(leaked, set(), role)

    def test_the_buyers_details_appear_nowhere_in_the_raw_response(self):
        self.mixed_estate()
        for role in sorted(ALL_ROLES):
            body = json.loads(self.get(role).content)
            visible_values = {str(value) for value in all_values(body)}
            for secret in ("Ada Obi", "0801", "ada@x.test", "1000", "400"):
                self.assertNotIn(secret, visible_values, f"{role} saw {secret!r}")


class PlotBoardAccessTests(BoardBase):
    def test_anonymous_and_companyless_users_are_refused(self):
        self.assertIn(self.client.get(self.url()).status_code, (401, 403))
        self.client.force_authenticate(self.no_company)
        self.assertEqual(self.client.get(self.url()).status_code, 403)

    def test_the_estate_parameter_is_required_and_must_be_a_number(self):
        self.login("md")
        base = reverse("plot-board")
        for query in ("", "?estate=", "?estate=%20", "?estate=abc", "?estate=-1", "?estate=1.5", "?estate=" + "9" * 30):
            response = self.client.get(base + query)
            self.assertEqual(response.status_code, 400, query)
            self.assertIn("estate", response.data)

    def test_an_unknown_estate_is_404(self):
        self.login("md")
        self.assertEqual(self.client.get(reverse("plot-board") + "?estate=999999").status_code, 404)

    def test_another_companys_estate_does_not_exist_for_the_caller(self):
        self.plot("X-1", estate=self.estate_b, company=self.b, actor=self.user_b)
        for role in sorted(ALL_ROLES):
            self.assertEqual(self.get(role, estate=self.estate_b).status_code, 404, role)
        self.client.force_authenticate(self.user_b)
        self.assertEqual(self.client.get(self.url(self.estate)).status_code, 404)             # and the other way
        self.assertEqual(self.client.get(self.url(self.estate_b)).data["count"], 1)

    def test_the_board_is_read_only(self):
        self.login("md")
        for method in ("post", "put", "patch", "delete"):
            self.assertEqual(getattr(self.client, method)(self.url(), {}, format="json").status_code, 405, method)


class PlotBoardIsolationTests(BoardBase):
    """Cross-company mismatches the database does not forbid. They must never leak status across companies."""

    def test_a_sale_of_another_company_on_this_plot_does_not_make_it_sold_here(self):
        plot = self.plot("A-1")
        buyer_b = Buyer.objects.create(company=self.b, full_name="Bayo Lawal")
        foreign = Sale.objects.create(company=self.b, plot=plot, buyer=buyer_b, price=500, sale_date=date(2026, 1, 5))
        for role in sorted(ALL_ROLES):
            row = self.get(role).data["plots"][0]
            self.assertEqual(row["status"], "available", role)
            self.assertNotIn(foreign.pk, [row.get("active_sale_id")])
        self.assertEqual(self.get("md").data["plots"][0]["active_sale_id"], None)

    def test_a_plot_of_another_company_under_this_estate_is_not_listed(self):
        self.plot("A-1")
        Plot.objects.create(company=self.b, estate=self.estate, plot_number="X-1")
        body = self.get("md").data
        self.assertEqual([r["plot_number"] for r in body["plots"]], ["A-1"])
        self.assertEqual(body["count"], 1)

    def test_company_bs_board_is_not_affected_by_company_as_sales(self):
        self.plot("A-1")
        self.sell(Plot.objects.get(plot_number="A-1"))
        plot_b = self.plot("X-1", estate=self.estate_b, company=self.b, actor=self.user_b)
        self.client.force_authenticate(self.user_b)
        row = self.client.get(self.url(self.estate_b)).data["plots"][0]
        self.assertEqual((row["plot_number"], row["status"]), ("X-1", "available"))
        self.assertEqual(row["id"], plot_b.pk)


class PlotBoardScaleTests(BoardBase):
    def populate(self, prefix, plots, sold):
        Plot.objects.bulk_create([Plot(company=self.a, estate=self.estate, plot_number=f"{prefix}-{n}")
                                  for n in range(plots)])
        for plot in Plot.objects.filter(estate=self.estate, plot_number__startswith=f"{prefix}-")[:sold]:
            Sale.objects.create(company=self.a, plot=plot, buyer=self.buyer, price=1000, amount_paid=1000,
                                sale_date=date(2026, 1, 5))

    def test_the_number_of_queries_does_not_depend_on_the_number_of_plots(self):
        self.populate("B", 5, 2)
        self.login("md")
        with self.assertNumQueries(2):                                         # the estate, then all the plots
            small = self.client.get(self.url())
        self.populate("C", 300, 120)
        with self.assertNumQueries(2):
            large = self.client.get(self.url())
        self.assertEqual((len(small.data["plots"]), len(large.data["plots"])), (5, 305))
        self.assertEqual(sum(1 for r in large.data["plots"] if r["status"] == "sold"), 122)

    @override_settings(INVENTORY_BOARD_MAX_PLOTS=3)
    def test_a_board_larger_than_the_ceiling_is_cut_and_says_so(self):
        for number in ("A-1", "A-2", "A-3", "A-4", "A-5"):
            self.plot(number)
        self.login("md")
        with self.assertNumQueries(3):                                         # plus one to count the whole estate
            body = self.client.get(self.url()).data
        self.assertEqual((body["truncated"], body["count"]), (True, 5))
        self.assertEqual([r["plot_number"] for r in body["plots"]], ["A-1", "A-2", "A-3"])

    @override_settings(INVENTORY_BOARD_MAX_PLOTS=5)
    def test_exactly_the_ceiling_is_not_truncated(self):
        for number in ("A-1", "A-2", "A-3", "A-4", "A-5"):
            self.plot(number)
        body = self.get("md").data
        self.assertEqual((body["truncated"], body["count"], len(body["plots"])), (False, 5, 5))
