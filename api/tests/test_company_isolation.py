from datetime import date

from django.urls import reverse
from rest_framework.test import APIRequestFactory, APITestCase, force_authenticate
from rest_framework.views import APIView
from rest_framework.response import Response

from api.permissions import ALL_ROLES, MATRIX, ActionPermission, can
from tracker import services
from tracker.models import Buyer, Company, Department, Estate, ObligationType, Plot, User

ENDPOINTS = ["estate", "plot", "buyer", "sale", "department", "user"]


class ApiBase(APITestCase):
    def setUp(self):
        self.a = Company.objects.create(name="A Homes")
        self.b = Company.objects.create(name="B Homes")
        self.dept_a = Department.objects.create(company=self.a, name="Legal")
        self.dept_b = Department.objects.create(company=self.b, name="Legal")
        self.ua = User.objects.create_user("ua", password="pw-12345-x", company=self.a, role="manager", department=self.dept_a)
        self.ub = User.objects.create_user("ub", password="pw-12345-x", company=self.b, role="manager", department=self.dept_b)
        self.lonely = User.objects.create_user("lonely", password="pw-12345-x", role="md")
        self.data = {}
        for co, user in ((self.a, self.ua), (self.b, self.ub)):
            estate = Estate.objects.create(company=co, name=f"Estate {co.name}")
            plot = Plot.objects.create(company=co, estate=estate, plot_number="P-1")
            buyer = Buyer.objects.create(company=co, full_name=f"Buyer {co.name}")
            ObligationType.objects.create(company=co, name="Deed", trigger="on_full_payment", sla_days=90)
            sale = services.create_sale(co, plot, buyer, 1000, date(2026, 9, 1), amount_paid=1000)
            self.data[co.pk] = {"estate": estate, "plot": plot, "buyer": buyer, "sale": sale,
                                "department": self.dept_a if co is self.a else self.dept_b, "user": user}

    def url(self, name, suffix="list", pk=None):
        return reverse(f"{name}-{suffix}", args=[pk] if pk else None)


class CompanyIsolationTests(ApiBase):
    def test_lists_only_return_own_company(self):
        self.client.force_authenticate(self.ua)
        for name in ENDPOINTS:
            r = self.client.get(self.url(name))
            self.assertEqual(r.status_code, 200, name)
            ids = {row["id"] for row in r.data["results"]}
            self.assertIn(self.data[self.a.pk][name].pk, ids, name)
            self.assertNotIn(self.data[self.b.pk][name].pk, ids, name)

    def test_other_company_detail_is_404(self):
        self.client.force_authenticate(self.ua)
        for name in ENDPOINTS:
            r = self.client.get(self.url(name, "detail", self.data[self.b.pk][name].pk))
            self.assertEqual(r.status_code, 404, name)

    def test_own_company_detail_is_200(self):
        self.client.force_authenticate(self.ua)
        for name in ENDPOINTS:
            r = self.client.get(self.url(name, "detail", self.data[self.a.pk][name].pk))
            self.assertEqual(r.status_code, 200, name)

    def test_reference_endpoints_are_read_only(self):
        self.client.force_authenticate(self.ua)
        for name in ENDPOINTS:
            own = self.data[self.a.pk][name].pk
            self.assertEqual(self.client.post(self.url(name), {}, format="json").status_code, 405, name)
            self.assertEqual(self.client.patch(self.url(name, "detail", own), {}, format="json").status_code, 405, name)
            self.assertEqual(self.client.delete(self.url(name, "detail", own)).status_code, 405, name)

    def test_user_without_company_is_denied_everywhere(self):
        self.client.force_authenticate(self.lonely)
        for name in ENDPOINTS:
            self.assertEqual(self.client.get(self.url(name)).status_code, 403, name)

    def test_anonymous_is_denied(self):
        for name in ENDPOINTS:
            self.assertIn(self.client.get(self.url(name)).status_code, (401, 403), name)

    def test_every_role_can_read_reference_data(self):
        for role in ALL_ROLES:
            user = User.objects.create_user(f"u_{role}", password="x", company=self.a, role=role)
            self.client.force_authenticate(user)
            self.assertEqual(self.client.get(self.url("estate")).status_code, 200, role)

    def test_filter_cannot_reach_other_company(self):
        self.client.force_authenticate(self.ua)
        other_estate = self.data[self.b.pk]["estate"].pk
        r = self.client.get(self.url("plot"), {"estate": other_estate})
        self.assertIn(r.status_code, (200, 400))
        if r.status_code == 200:
            self.assertEqual(r.data["results"], [])


class PermissionMatrixTests(ApiBase):
    def test_matrix_only_uses_known_roles(self):
        for action, roles in MATRIX.items():
            self.assertTrue(roles <= ALL_ROLES, action)
            self.assertTrue(roles, action)

    def test_spot_checks(self):
        sales = User(role="sales", company=self.a)
        staff = User(role="staff", company=self.a)
        viewer = User(role="viewer", company=self.a)
        for u in (sales, staff, viewer):
            u.pk = 999  # is_authenticated is True for any User instance
        self.assertFalse(can(sales, "import_data"))
        self.assertFalse(can(staff, "bulk_update"))
        self.assertTrue(can(viewer, "export"))
        self.assertTrue(can(sales, "buyer_message"))
        self.assertFalse(can(self.lonely, "view_reference"))  # no company

    def test_view_without_required_action_fails_closed(self):
        class Forgetful(APIView):
            permission_classes = [ActionPermission]

            def get(self, request):
                return Response({"secret": True})

        request = APIRequestFactory().get("/x/")
        force_authenticate(request, user=self.ua)
        self.assertEqual(Forgetful.as_view()(request).status_code, 403)
