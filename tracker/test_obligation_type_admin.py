from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import Permission
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone
from . import services
from .admin import ObligationTypeAdmin
from .models import Buyer, Company, Estate, ObligationType, Plot, User


class ObligationTypeAdminRenameTests(TestCase):
    def setUp(self):
        today = timezone.localdate()
        self.co = Company.objects.create(name="A Homes")
        self.used = ObligationType.objects.create(
            company=self.co, name="Deed", trigger="on_full_payment", sla_days=90
        )
        estate = Estate.objects.create(company=self.co, name="Green Valley")
        plot = Plot.objects.create(company=self.co, estate=estate, plot_number="P-1")
        buyer = Buyer.objects.create(company=self.co, full_name="Buyer One")
        services.create_sale(self.co, plot, buyer, 100, today, 100, today)
        self.assertTrue(self.used.obligations.exists())
        # Create this type after sale generation so it genuinely has no obligations.
        self.unused = ObligationType.objects.create(
            company=self.co, name="Unused Doc", trigger="on_sale", sla_days=30
        )
        self.staff = User.objects.create_user(
            "opsadmin", password="x", company=self.co, role="manager", is_staff=True
        )
        self.staff.user_permissions.set(
            Permission.objects.filter(
                content_type__app_label="tracker",
                codename__in=["view_obligationtype", "change_obligationtype"],
            )
        )

    def admin(self):
        return ObligationTypeAdmin(ObligationType, AdminSite())

    def request(self):
        request = RequestFactory().get("/")
        request.user = self.staff
        return request

    def change_url(self, obligation_type):
        return reverse("admin:tracker_obligationtype_change", args=[obligation_type.pk])

    def form(self, **extra):
        return {
            "company": self.co.pk,
            "trigger": "on_sale",
            "sla_days": 30,
            "default_department": "",
            "is_active": "on",
            **extra,
        }

    def test_the_name_is_read_only_for_a_used_type_only(self):
        self.assertIn("name", self.admin().get_readonly_fields(self.request(), self.used))
        self.assertNotIn("name", self.admin().get_readonly_fields(self.request(), self.unused))
        self.assertNotIn("name", self.admin().get_readonly_fields(self.request(), None))

    def test_the_change_page_shows_the_name_as_text_for_a_used_type(self):
        self.client.force_login(self.staff)
        self.assertNotContains(self.client.get(self.change_url(self.used)), 'name="name"')
        self.assertContains(self.client.get(self.change_url(self.unused)), 'name="name"')

    def test_posting_a_new_name_does_not_rename_a_used_type(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            self.change_url(self.used), self.form(name="Hacked", sla_days=45)
        )
        self.assertEqual(response.status_code, 302)
        self.used.refresh_from_db()
        self.assertEqual((self.used.name, self.used.sla_days), ("Deed", 45))

    def test_an_unused_type_can_still_be_renamed_in_the_admin(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            self.change_url(self.unused), self.form(name="Unused Doc Renamed")
        )
        self.assertEqual(response.status_code, 302)
        self.unused.refresh_from_db()
        self.assertEqual(self.unused.name, "Unused Doc Renamed")
