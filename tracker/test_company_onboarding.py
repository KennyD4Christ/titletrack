"""Tests for company onboarding: the shared service (Stage 1) and the onboard_company permission (Stage 2).

Later stages add the Django admin onboarding view and its authorization tests to this same file.
"""
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, connection, transaction
from django.test import TestCase, override_settings

from . import onboarding_services as svc
from .models import AccountEvent, Company, Department, User

GOOD = dict(company="Acme Estates", departments=["Legal", "Survey"], md_email="Ada@Acme.Test",
            md_first_name="Ada", md_last_name="Obi")
NOTES = dict(created_note="Created by a test", issued_note="Issued by a test")


def counts():
    return (Company.objects.count(), Department.objects.count(), User.objects.count(), AccountEvent.objects.count())


@override_settings(EMAIL_DELIVERY_ENABLED=False, SITE_URL="https://app.example.test")
class OnboardingServiceTests(TestCase):
    def test_creates_company_departments_and_an_md_with_no_password(self):
        result = svc.onboard_company(**GOOD, **NOTES)
        self.assertEqual(result.company.name, "Acme Estates")
        self.assertEqual(sorted(result.company.departments.values_list("name", flat=True)), ["Legal", "Survey"])
        self.assertEqual((result.md.role, result.md.username, result.md.email), ("md", "ada@acme.test", "ada@acme.test"))
        self.assertFalse(result.md.has_usable_password())
        self.assertFalse(result.md.is_staff or result.md.is_superuser)
        self.assertEqual(result.departments, ["Legal", "Survey"])

    def test_the_first_link_is_issued_and_not_delivered_here(self):
        result = svc.onboard_company(**GOOD, **NOTES)
        self.assertEqual(result.issued.user_id, result.md.pk)
        self.assertEqual(AccountEvent.objects.filter(action="link_revealed").count(), 0)     # caller delivers
        self.assertNotIn(result.issued.token, repr(result))

    def test_the_audit_trail_has_the_notes_and_no_actor_by_default(self):
        svc.onboard_company(**GOOD, **NOTES)
        self.assertEqual(sorted(AccountEvent.objects.values_list("action", "note", "actor")),
                         [("created", "Created by a test", None), ("setup_link_issued", "Issued by a test", None)])

    def test_an_actor_is_recorded_on_the_created_event_only(self):
        operator = User.objects.create_user("operator", password="x", is_staff=True)       # no company
        svc.onboard_company(**GOOD, **NOTES, actor=operator)
        self.assertEqual(AccountEvent.objects.get(action="created").actor, operator)
        self.assertIsNone(AccountEvent.objects.get(action="setup_link_issued").actor)

    def test_every_rule_refuses_and_names_the_field_and_writes_nothing(self):
        existing = Company.objects.create(name="Real   Estates")
        User.objects.create_user("taken@x.test", email="Taken@X.test", company=existing)
        User.objects.create_user("legacy_name", company=existing)
        before = counts()
        cases = [
            (dict(company="Demo Homes"), "company"), (dict(company="  demo estates"), "company"),
            (dict(company="real estates"), "company"), (dict(company="   "), "company"),
            (dict(company="x" * 201), "company"),
            (dict(md_email="not-an-email"), "md_email"), (dict(md_email="TAKEN@x.test"), "md_email"),
            (dict(md_email="Legacy_Name"), "md_email"),
            (dict(md_first_name=" "), "md_first_name"), (dict(md_last_name=""), "md_last_name"),
            (dict(md_first_name="x" * 151), "md_first_name"),
            (dict(departments=["Legal", "legal"]), "departments"), (dict(departments=["Legal", "  "]), "departments"),
            (dict(departments=["y" * 101]), "departments"),
        ]
        for change, field in cases:
            with self.assertRaises(svc.OnboardingError, msg=str(change)) as ctx:
                svc.onboard_company(**{**GOOD, **change}, **NOTES)
            self.assertEqual(ctx.exception.field, field, msg=str(change))
        self.assertEqual(counts(), before)

    def test_a_failure_part_way_leaves_nothing_behind(self):
        before = counts()
        with patch("tracker.account_services.issue_link", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                svc.onboard_company(**GOOD, **NOTES)
        self.assertEqual(counts(), before)

    def test_a_duplicate_created_by_a_rival_request_is_reported_precisely(self):
        other = Company.objects.create(name="Other")
        stale = svc.validate_onboarding(**GOOD)          # passes now: nobody has the email yet
        User.objects.create_user("ada@acme.test", email="ada@acme.test", company=other)    # the rival gets there
        real, calls = svc.validate_onboarding, []

        def first_call_is_stale(**kwargs):
            if not calls:
                calls.append(1)
                return stale
            return real(**kwargs)

        before = counts()
        with patch("tracker.onboarding_services.validate_onboarding", side_effect=first_call_is_stale):
            with self.assertRaises(svc.OnboardingError) as ctx:
                svc.onboard_company(**GOOD, **NOTES)
        self.assertEqual(ctx.exception.field, "md_email")
        self.assertEqual(counts(), before)               # the half-built company was rolled back

    def test_an_unexpected_integrity_error_surfaces_instead_of_being_disguised(self):
        before = counts()
        with patch("tracker.onboarding_services.Department.objects.create", side_effect=IntegrityError("odd")):
            with self.assertRaises(IntegrityError):
                svc.onboard_company(**GOOD, **NOTES)
        self.assertEqual(counts(), before)

    def test_the_company_name_lock_is_taken_on_the_normalised_name(self):
        with patch.object(svc, "_lock_company_name") as lock:
            svc.onboard_company(**{**GOOD, "company": "  Acme   ESTATES "}, **NOTES)
        lock.assert_called_once_with("acme estates")

    def test_the_lock_statement_is_valid_on_postgresql(self):
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL only")
        with transaction.atomic():
            svc._lock_company_name("acme estates")
            svc._lock_company_name("acme estates")        # the same session may take it again


@override_settings(EMAIL_DELIVERY_ENABLED=False, SITE_URL="https://app.example.test")
class CommandStillSpeaksCommandLineTests(TestCase):
    def test_the_empty_department_message_keeps_its_command_line_wording(self):
        with self.assertRaises(CommandError) as ctx:
            call_command("onboard_company", company="Acme", department=["Legal", " "], md_email="a@b.test",
                         md_first_name="A", md_last_name="B", stdout=StringIO(), stderr=StringIO())
        self.assertEqual(str(ctx.exception), "A --department value is empty.")


class CompanyOnboardPermissionTests(TestCase):
    """The permission that will mark a user as a platform operator. Declaring it grants it to nobody."""

    def permission(self):
        return Permission.objects.get(content_type__app_label="tracker", codename="onboard_company")

    def test_the_permission_exists_on_company(self):
        perm = self.permission()
        self.assertEqual(perm.content_type.model, "company")
        self.assertEqual(perm.name, "Can onboard a new company")

    def test_the_default_company_permissions_are_unchanged(self):
        codenames = set(Permission.objects.filter(content_type__app_label="tracker", content_type__model="company")
                        .values_list("codename", flat=True))
        self.assertEqual(codenames, {"add_company", "change_company", "delete_company", "view_company",
                                     "onboard_company"})

    def test_nobody_holds_it_by_default_not_even_a_superuser(self):
        root = User.objects.create_superuser("root", password="x")
        perm = self.permission()
        self.assertFalse(perm.user_set.exists())
        self.assertFalse(perm.group_set.exists())
        self.assertFalse(root.user_permissions.filter(pk=perm.pk).exists())
