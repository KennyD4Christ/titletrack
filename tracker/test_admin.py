from django.contrib import admin
from django.test import RequestFactory, TestCase

from .models import Company, Estate, Obligation, ObligationEvent, Plot, Sale, User


class AdminRulesTests(TestCase):
    def setUp(self):
        self.a = Company.objects.create(name="A")
        self.b = Company.objects.create(name="B")
        Estate.objects.create(company=self.a, name="EA")
        Estate.objects.create(company=self.b, name="EB")
        self.staff = User.objects.create_user("staff", password="x", company=self.a, is_staff=True)
        self.root = User.objects.create_superuser("root", password="x")

    def request(self, user):
        req = RequestFactory().get("/admin/")
        req.user = user
        return req

    def test_staff_sees_only_own_company_rows(self):
        ma = admin.site._registry[Estate]
        self.assertEqual([e.name for e in ma.get_queryset(self.request(self.staff))], ["EA"])
        self.assertEqual(ma.get_queryset(self.request(self.root)).count(), 2)

    def test_foreign_key_choices_are_limited_to_own_company(self):
        ma = admin.site._registry[Plot]
        field = ma.formfield_for_foreignkey(Plot._meta.get_field("estate"), self.request(self.staff))
        self.assertEqual([e.name for e in field.queryset], ["EA"])

    def test_companies_cannot_be_created_through_the_default_admin_add_form(self):
        ma = admin.site._registry[Company]
        for user in (self.root, self.staff):
            self.assertFalse(ma.has_add_permission(self.request(user)), user.username)

    def test_money_ownership_and_history_are_read_only_in_admin(self):
        for model in (Sale, Obligation, ObligationEvent):
            ma = admin.site._registry[model]
            req = self.request(self.root)
            self.assertFalse(ma.has_add_permission(req), model)
            self.assertFalse(ma.has_change_permission(req), model)
            self.assertFalse(ma.has_delete_permission(req), model)
