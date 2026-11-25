"""Estate and Plot are read-only in the Django admin (inventory Phase 1, stage 4): every change goes through the
inventory services, which check the rules and write the audit trail. The admin stays useful for looking."""
from django.contrib import admin
from django.contrib.auth.models import Permission
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse

from tracker import inventory_services as inv
from tracker.models import Company, Estate, InventoryEvent, Plot, User


class InventoryAdminReadOnlyTests(TestCase):
    def setUp(self):
        self.a = Company.objects.create(name="A")
        self.b = Company.objects.create(name="B")
        self.root = User.objects.create_superuser("root", password="x")
        self.staff = User.objects.create_user("staff", password="x", company=self.a, is_staff=True)
        for codename in ("view", "add", "change", "delete"):
            for model in ("estate", "plot"):
                self.staff.user_permissions.add(Permission.objects.get(codename=f"{codename}_{model}"))
        self.md = User.objects.create_user("md", password="x", company=self.a, role="md")
        self.estate = inv.create_estate(self.a, self.md, name="Green Valley")
        self.plot = inv.register_plot(self.a, self.md, estate=self.estate, plot_number="A-1")
        self.other = inv.create_estate(self.b, User.objects.create_user("md_b", password="x", company=self.b, role="md"),
                                       name="Palm Heights")

    def request(self, user):
        request = RequestFactory().get("/admin/")
        request.user = user
        return request

    def test_nobody_can_add_change_or_delete_through_the_admin_permissions(self):
        for model in (Estate, Plot):
            ma = admin.site._registry[model]
            for user in (self.root, self.staff):
                request = self.request(user)
                self.assertFalse(ma.has_add_permission(request), (model, user.username))
                self.assertFalse(ma.has_change_permission(request), (model, user.username))
                self.assertFalse(ma.has_delete_permission(request), (model, user.username))

    def test_the_pages_that_would_write_are_closed_even_to_a_superuser(self):
        client = Client()
        client.force_login(self.root)
        before = (Estate.objects.count(), Plot.objects.count(), InventoryEvent.objects.count())
        self.assertEqual(client.get(reverse("admin:tracker_estate_add")).status_code, 403)
        self.assertEqual(client.post(reverse("admin:tracker_estate_add"), {"company": self.a.pk, "name": "Sneaky"}).status_code, 403)
        self.assertEqual(client.post(reverse("admin:tracker_plot_add"),
                                     {"company": self.a.pk, "estate": self.estate.pk, "plot_number": "Z-1"}).status_code, 403)
        for name, obj in (("estate", self.estate), ("plot", self.plot)):
            self.assertEqual(client.post(reverse(f"admin:tracker_{name}_delete", args=[obj.pk]), {"post": "yes"}).status_code, 403)
            response = client.post(reverse(f"admin:tracker_{name}_change", args=[obj.pk]),
                                   {"name": "Hacked", "plot_number": "Hacked", "location": "x"})
            self.assertIn(response.status_code, (302, 403))
        self.assertEqual((Estate.objects.count(), Plot.objects.count(), InventoryEvent.objects.count()), before)
        self.assertEqual(Estate.objects.get(pk=self.estate.pk).name, "Green Valley")
        self.assertEqual(Plot.objects.get(pk=self.plot.pk).plot_number, "A-1")

    def test_the_bulk_delete_action_is_gone(self):
        client = Client()
        client.force_login(self.root)
        for name, obj in (("estate", self.estate), ("plot", self.plot)):
            response = client.post(reverse(f"admin:tracker_{name}_changelist"),
                                   {"action": "delete_selected", "_selected_action": [obj.pk], "post": "yes"})
            self.assertIn(response.status_code, (200, 302))
        self.assertEqual((Estate.objects.filter(pk=self.estate.pk).exists(), Plot.objects.filter(pk=self.plot.pk).exists()),
                         (True, True))

    def test_looking_still_works_and_stays_inside_the_staff_users_company(self):
        for model in (Estate, Plot):
            ma = admin.site._registry[model]
            self.assertTrue(ma.has_view_permission(self.request(self.staff)), model)
        self.assertEqual([e.name for e in admin.site._registry[Estate].get_queryset(self.request(self.staff))], ["Green Valley"])
        self.assertEqual(admin.site._registry[Estate].get_queryset(self.request(self.root)).count(), 2)
        client = Client()
        client.force_login(self.staff)
        self.assertEqual(client.get(reverse("admin:tracker_estate_changelist")).status_code, 200)
        self.assertEqual(client.get(reverse("admin:tracker_plot_changelist")).status_code, 200)
        self.assertEqual(client.get(reverse("admin:tracker_estate_change", args=[self.estate.pk])).status_code, 200)
        self.assertEqual(client.get(reverse("admin:tracker_estate_change", args=[self.other.pk])).status_code, 302)  # not theirs

    def test_the_company_scoping_methods_the_older_admin_tests_rely_on_are_unchanged(self):
        ma = admin.site._registry[Plot]
        field = ma.formfield_for_foreignkey(Plot._meta.get_field("estate"), self.request(self.staff))
        self.assertEqual([e.name for e in field.queryset], ["Green Valley"])
