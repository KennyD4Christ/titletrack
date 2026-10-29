from django.contrib import admin
from django.contrib.auth.models import Group, Permission
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse

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

    def test_non_superuser_cannot_edit_platform_privilege_fields_in_user_admin(self):
        ma = admin.site._registry[User]
        request = self.request(self.staff)

        fieldsets = ma.get_fieldsets(request, obj=self.staff)
        visible_fields = {
            field
            for _, options in fieldsets
            for field in options.get("fields", ())
        }

        protected = {
            "is_staff",
            "is_superuser",
            "groups",
            "user_permissions",
        }
        self.assertTrue(protected.isdisjoint(visible_fields))

        form_class = ma.get_form(request, obj=self.staff)
        self.assertTrue(protected.isdisjoint(form_class.base_fields))

    def test_superuser_retains_platform_privilege_fields_in_user_admin(self):
        ma = admin.site._registry[User]
        request = self.request(self.root)

        form_class = ma.get_form(request, obj=self.staff)

        protected = {
            "is_staff",
            "is_superuser",
            "groups",
            "user_permissions",
        }
        self.assertTrue(protected.issubset(form_class.base_fields))

    def test_non_superuser_forged_post_cannot_grant_platform_privileges(self):
        target = User.objects.create_user(
            "target",
            password="x",
            company=self.a,
            is_staff=False,
            is_superuser=False,
        )
        self.staff.user_permissions.clear()
        self.staff.is_staff = True
        self.staff.save()

        # Give the actor the ordinary permissions needed to enter user admin.
        from django.contrib.contenttypes.models import ContentType

        platform_group = Group.objects.create(name="Platform Admins")

        content_type = ContentType.objects.get_for_model(User)
        change_permission = Permission.objects.get(
            content_type=content_type,
            codename="change_user",
        )
        view_permission = Permission.objects.get(
            content_type=content_type,
            codename="view_user",
        )
        self.staff.user_permissions.add(change_permission, view_permission)

        client = Client()
        client.force_login(self.staff)

        response = client.post(
            reverse("admin:tracker_user_change", args=[target.pk]),
            {
                "username": target.username,
                "first_name": target.first_name,
                "last_name": target.last_name,
                "email": target.email,
                "company": str(self.a.pk),
                "role": target.role,
                "is_staff": "on",
                "is_superuser": "on",
                "groups": [str(platform_group.pk)],
                "user_permissions": [str(change_permission.pk)],
                "_save": "Save",
            },
        )

        target.refresh_from_db()
        self.assertFalse(target.is_staff)
        self.assertFalse(target.is_superuser)
        self.assertEqual(
            set(target.groups.values_list("pk", flat=True)),
            set(),
        )
        self.assertEqual(
            set(target.user_permissions.values_list("pk", flat=True)),
            set(),
        )
        self.assertIn(response.status_code, (200, 302))

    def test_non_superuser_cannot_change_another_company_user(self):
        target = User.objects.create_user(
            "foreign",
            password="x",
            company=self.b,
            first_name="Before",
        )

        from django.contrib.auth.models import Permission
        from django.contrib.contenttypes.models import ContentType

        content_type = ContentType.objects.get_for_model(User)
        self.staff.user_permissions.add(
            Permission.objects.get(
                content_type=content_type,
                codename="change_user",
            ),
            Permission.objects.get(
                content_type=content_type,
                codename="view_user",
            ),
        )

        client = Client()
        client.force_login(self.staff)
        response = client.post(
            reverse("admin:tracker_user_change", args=[target.pk]),
            {
                "username": target.username,
                "first_name": "Changed",
                "company": str(self.a.pk),
                "role": target.role,
                "_save": "Save",
            },
        )

        target.refresh_from_db()
        self.assertNotEqual(target.first_name, "Changed")
        self.assertEqual(target.company_id, self.b.pk)
        self.assertIn(response.status_code, (403, 404, 302))
