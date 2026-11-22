"""Who may use the inventory endpoints and the plot board. The permission check comes first: a role that may not
register is refused before anything is read or written, even with a perfectly valid request."""
from django.test import SimpleTestCase
from django.urls import reverse

from api.permissions import ALL_ROLES, MATRIX, MD, MANAGER, ActionPermission
from api.tests.inventory_fixtures import InventoryApiBase
from api.urls import router
from api.views.inventory import InventoryEstateViewSet, InventoryPlotViewSet
from api.views.plot_board import PlotBoardView
from tracker import inventory_services as inv
from tracker.models import Estate, InventoryEvent, Plot

MANAGERS = {MD, MANAGER}


class MatrixTests(SimpleTestCase):
    def test_the_two_actions(self):
        self.assertEqual(MATRIX["view_plot_board"], ALL_ROLES)
        self.assertEqual(MATRIX["manage_inventory"], MANAGERS)

    def test_the_views_declare_their_actions_and_nothing_is_left_unmapped(self):
        self.assertEqual(PlotBoardView.required_action, "view_plot_board")
        self.assertIn(ActionPermission, PlotBoardView.permission_classes)
        routed = {}
        for route in router.urls:
            callback = getattr(route, "callback", None)
            cls = getattr(callback, "cls", None)
            if cls in (InventoryEstateViewSet, InventoryPlotViewSet):
                routed.setdefault(cls, set()).update(callback.actions.values())
        self.assertEqual(set(routed), {InventoryEstateViewSet, InventoryPlotViewSet})
        for cls, actions in routed.items():
            self.assertEqual(actions, set(cls.required_actions), cls.__name__)     # every routed action is mapped, no extras
            self.assertEqual(set(cls.required_actions.values()), {"manage_inventory"})


class RoleByEndpointTests(InventoryApiBase):
    def setUp(self):
        super().setUp()
        self.plot_ = self.plot("A-1")
        entries = [{"labels": ["Z-1", "Z-2"]}]
        preview = inv.preview_bulk(self.a, self.md, estate=self.estate, entries=entries)
        # name, method, args, a VALID body (would succeed for a manager), an invalid body (reaches validation)
        self.endpoints = [
            ("inventory-estate-list", "post", None, {"name": "Zed Estate"}, {}),
            ("inventory-estate-detail", "patch", [self.estate.pk], {"name": "Renamed"}, {}),
            ("inventory-plot-list", "post", None, {"estate": self.estate.pk, "plot_number": "Z-9"}, {}),
            ("inventory-plot-detail", "patch", [self.plot_.pk], {"plot_number": "Z-8"}, {}),
            ("inventory-plot-remove", "post", [self.plot_.pk], {"reason": "registered by mistake"}, {}),
            ("inventory-plot-bulk-preview", "post", None, {"estate": self.estate.pk, "entries": entries}, {}),
            ("inventory-plot-bulk-commit", "post", None,
             {"estate": self.estate.pk, "entries": entries, "input_fingerprint": preview["input_fingerprint"],
              "preview_fingerprint": preview["preview_fingerprint"]}, {}),
        ]

    def state(self):
        return (Estate.objects.count(), Plot.objects.count(), InventoryEvent.objects.count(),
                list(Estate.objects.order_by("pk").values_list("name", flat=True)),
                list(Plot.objects.order_by("pk").values_list("plot_number", flat=True)))

    def call(self, name, method, args, body):
        return getattr(self.client, method)(reverse(name, args=args), body, format="json")

    def test_roles_that_may_not_manage_inventory_are_refused_with_a_valid_request_and_nothing_changes(self):
        for role in sorted(ALL_ROLES - MANAGERS):
            self.login(role)
            for name, method, args, valid, _ in self.endpoints:
                before = self.state()
                response = self.call(name, method, args, valid)
                self.assertEqual(response.status_code, 403, f"{role} {name}")
                self.assertEqual(self.state(), before, f"{role} {name} changed something")

    def test_roles_that_may_manage_inventory_get_past_the_permission_check(self):
        for role in sorted(MANAGERS):
            self.login(role)
            for name, method, args, _, invalid in self.endpoints:
                response = self.call(name, method, args, invalid)
                self.assertEqual(response.status_code, 400, f"{role} {name}: {response.data}")

    def test_both_managing_roles_can_really_register(self):
        for role, number in (("md", "M-1"), ("manager", "M-2")):
            self.login(role)
            response = self.call("inventory-plot-list", "post", None, {"estate": self.estate.pk, "plot_number": number})
            self.assertEqual(response.status_code, 201, role)
            self.assertEqual(InventoryEvent.objects.get(plot_label=number).actor, self.users[role])

    def test_anonymous_callers_are_refused_everywhere(self):
        for name, method, args, valid, _ in self.endpoints:
            before = self.state()
            self.assertIn(self.call(name, method, args, valid).status_code, (401, 403), name)
            self.assertEqual(self.state(), before, name)
        self.assertIn(self.client.get(reverse("plot-board") + f"?estate={self.estate.pk}").status_code, (401, 403))

    def test_a_user_with_no_company_is_refused_everywhere(self):
        self.client.force_authenticate(self.no_company)
        for name, method, args, valid, _ in self.endpoints:
            before = self.state()
            self.assertEqual(self.call(name, method, args, valid).status_code, 403, name)
            self.assertEqual(self.state(), before, name)
        self.assertEqual(self.client.get(reverse("plot-board") + f"?estate={self.estate.pk}").status_code, 403)

    def test_another_companys_manager_cannot_reach_this_companys_records(self):
        """Company B's MD has the permission but not the company: every request that names company A's records is
        refused as if they did not exist, and only their own estate creation succeeds (in their own company)."""
        def company_a():
            return (list(Estate.objects.filter(company=self.a).values_list("name", flat=True)),
                    list(Plot.objects.filter(company=self.a).values_list("plot_number", flat=True)),
                    InventoryEvent.objects.filter(company=self.a).count())

        before = company_a()
        self.client.force_authenticate(self.user_b)
        expected = {
            "inventory-estate-list": 201,            # creates an estate, in company B
            "inventory-estate-detail": 404,
            "inventory-plot-list": 400,              # company A's estate is not a valid choice
            "inventory-plot-detail": 404,
            "inventory-plot-remove": 404,
            "inventory-plot-bulk-preview": 400,
            "inventory-plot-bulk-commit": 400,
        }
        for name, method, args, valid, _ in self.endpoints:
            self.assertEqual(self.call(name, method, args, valid).status_code, expected[name], name)
        self.assertEqual(company_a(), before)
        zed = Estate.objects.get(name="Zed Estate")
        self.assertEqual(zed.company, self.b)
