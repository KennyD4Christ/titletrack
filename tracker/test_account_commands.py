import re
from io import StringIO
from unittest.mock import patch

from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.contrib.auth import authenticate
from django.test import TestCase, override_settings

from . import account_services as acc
from .models import AccountEvent, Company, Department, ObligationType, User

STRONG = "Tr1cky-Horse-Battery-42"
GOOD = dict(company="Acme Estates", md_email="Ada@Acme.Test", md_first_name="Ada", md_last_name="Obi")


def run(name, *args, **options):
    out, err = StringIO(), StringIO()
    call_command(name, *args, stdout=out, stderr=err, **options)
    return out.getvalue()


def link_in(text):
    match = re.search(r"https?://\S+#token=(\S+)", text)
    return (match.group(0), match.group(1)) if match else (None, None)


def counts():
    return (Company.objects.count(), Department.objects.count(), User.objects.count(), AccountEvent.objects.count())


@override_settings(EMAIL_DELIVERY_ENABLED=False, SITE_URL="https://app.example.test")
class OnboardCompanyTests(TestCase):
    def test_creates_company_departments_and_an_md_with_no_password(self):
        out = run("onboard_company", department=["Legal", "Survey"], **GOOD)
        company = Company.objects.get(name="Acme Estates")
        self.assertEqual(sorted(company.departments.values_list("name", flat=True)), ["Legal", "Survey"])
        md = User.objects.get(company=company)
        self.assertEqual((md.role, md.username, md.email, md.first_name, md.last_name),
                         ("md", "ada@acme.test", "ada@acme.test", "Ada", "Obi"))
        self.assertFalse(md.has_usable_password())
        self.assertFalse(md.is_staff or md.is_superuser)
        self.assertEqual(ObligationType.objects.filter(company=company).count(), 0)     # no document types
        self.assertIn("created", out)

    def test_prints_the_setup_link_once_with_its_expiry_and_the_link_works(self):
        out = run("onboard_company", **GOOD)
        url, token = link_in(out)
        self.assertEqual(out.count("#token="), 1)
        self.assertIn("expires in 3 days", out)
        self.assertTrue(url.startswith("https://app.example.test/set-password#token="))
        self.assertNotIn("password:", out.lower().replace("set-password", ""))
        acc.complete_password_link(token, acc.SETUP, STRONG)
        self.assertIsNotNone(authenticate(username="ADA@acme.test".lower(), password=STRONG))

    def test_history_has_created_issued_and_revealed_and_no_secret(self):
        out = run("onboard_company", **GOOD)
        _, token = link_in(out)
        self.assertEqual(sorted(AccountEvent.objects.values_list("action", flat=True)),
                         ["created", "link_revealed", "setup_link_issued"])
        for e in AccountEvent.objects.all():
            self.assertEqual(e.target_email, "ada@acme.test")
            self.assertIsNone(e.actor)
            self.assertNotIn(token, f"{e.changes}{e.note}{e.target_email}")

    def test_no_departments_is_allowed(self):
        run("onboard_company", **GOOD)
        self.assertEqual(Department.objects.count(), 0)

    @override_settings(EMAIL_DELIVERY_ENABLED=True)
    def test_email_enabled_and_sent_does_not_print_the_link(self):
        out = run("onboard_company", **GOOD)
        self.assertNotIn("#token", out)
        self.assertIn("emailed", out)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["ada@acme.test"])
        self.assertIn("https://app.example.test/set-password#token=", mail.outbox[0].body)
        self.assertEqual(AccountEvent.objects.filter(action="link_revealed").count(), 0)

    @override_settings(EMAIL_DELIVERY_ENABLED=True)
    def test_email_failure_stops_with_a_clear_message_and_keeps_the_company(self):
        with patch("tracker.account_services.send_mail", side_effect=OSError("down")), \
                self.assertLogs("tracker.account_services", "WARNING"):
            with self.assertRaises(CommandError) as ctx:
                run("onboard_company", **GOOD)
        self.assertNotIn("#token", str(ctx.exception))
        self.assertIn("--show-link-if-email-fails", str(ctx.exception))
        self.assertTrue(Company.objects.filter(name="Acme Estates").exists())        # created, only the email failed
        self.assertEqual(AccountEvent.objects.filter(action="email_failed").count(), 1)
        self.assertEqual(AccountEvent.objects.filter(action="link_revealed").count(), 0)

    @override_settings(EMAIL_DELIVERY_ENABLED=True)
    def test_explicit_flag_shows_the_link_after_an_email_failure(self):
        with patch("tracker.account_services.send_mail", side_effect=OSError("down")), \
                self.assertLogs("tracker.account_services", "WARNING"):
            out = run("onboard_company", show_link_if_email_fails=True, **GOOD)
        url, token = link_in(out)
        self.assertIsNotNone(token)
        self.assertEqual(sorted(AccountEvent.objects.filter(action__in=["email_failed", "link_revealed"])
                                .values_list("action", flat=True)), ["email_failed", "link_revealed"])
        acc.complete_password_link(token, acc.SETUP, STRONG)

    def test_everything_is_refused_before_anything_is_written(self):
        existing = Company.objects.create(name="Real   Estates")
        User.objects.create_user("taken@x.test", email="Taken@X.test", company=existing)
        User.objects.create_user("legacy_name", company=existing)
        before = counts()
        long_email = "a" * 64 + "@" + ".".join(["b" * 63, "c" * 63, "d" * 10]) + ".com"
        bad = [
            dict(company="Demo Homes"), dict(company="  demo estates"), dict(company="DEMOLITION LTD"),
            dict(company="real estates"), dict(company=" Real Estates "), dict(company="   "),
            dict(company="x" * 201),
            dict(md_email="not-an-email"), dict(md_email=long_email), dict(md_email="TAKEN@x.test"),
            dict(md_email="Legacy_Name"), dict(md_first_name=" "), dict(md_last_name=""),
            dict(md_first_name="x" * 151),
            dict(department=["Legal", "legal"]), dict(department=["Legal", "  "]), dict(department=["y" * 101]),
        ]
        for change in bad:
            args = {**GOOD, "department": [], **change}
            with self.assertRaises(CommandError, msg=str(change)):
                run("onboard_company", **args)
        self.assertEqual(counts(), before)

    def test_a_failure_part_way_leaves_nothing_behind(self):
        before = counts()
        with patch("tracker.account_services.issue_link", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                run("onboard_company", department=["Legal"], **GOOD)
        self.assertEqual(counts(), before)

    def test_the_same_company_cannot_be_onboarded_twice(self):
        run("onboard_company", **GOOD)
        with self.assertRaises(CommandError):
            run("onboard_company", **{**GOOD, "md_email": "other@acme.test"})
        self.assertEqual(Company.objects.count(), 1)


@override_settings(EMAIL_DELIVERY_ENABLED=False, SITE_URL="https://app.example.test")
class IssueSetupLinkTests(TestCase):
    def setUp(self):
        self.co = Company.objects.create(name="Acme Estates")
        self.md = User.objects.create_user("ada@acme.test", email="ada@acme.test", company=self.co, role="md")
        self.md.set_unusable_password()
        self.md.save()
        self.demo_md = User.objects.create_user("md_demohomes", password="x", company=self.co, role="md")
        self.manager = User.objects.create_user("mo@acme.test", email="mo@acme.test", password="x",
                                                company=self.co, role="manager")

    def test_issues_a_link_by_email_ignoring_case_and_the_link_works(self):
        out = run("issue_setup_link", email="ADA@acme.TEST")
        url, token = link_in(out)
        self.assertIn("expires in 3 days", out)
        acc.complete_password_link(token, acc.SETUP, STRONG)
        self.assertIsNotNone(authenticate(username="ada@acme.test", password=STRONG))

    def test_works_by_username_for_accounts_without_an_email(self):
        _, token = link_in(run("issue_setup_link", username="md_demohomes"))
        acc.complete_password_link(token, acc.SETUP, STRONG)

    def test_a_new_link_replaces_the_previous_one(self):
        _, first = link_in(run("issue_setup_link", email="ada@acme.test"))
        _, second = link_in(run("issue_setup_link", email="ada@acme.test"))
        with self.assertRaises(acc.InvalidLink):
            acc.complete_password_link(first, acc.SETUP, STRONG)
        acc.complete_password_link(second, acc.SETUP, STRONG)

    def test_history_records_the_issue_and_the_reveal_without_the_secret(self):
        _, token = link_in(run("issue_setup_link", email="ada@acme.test"))
        self.assertEqual(sorted(AccountEvent.objects.values_list("action", flat=True)),
                         ["link_revealed", "setup_link_issued"])
        for e in AccountEvent.objects.all():
            self.assertEqual((e.company, e.target_user, e.target_email), (self.co, self.md, "ada@acme.test"))
            if e.action == "setup_link_issued":
                self.assertIn("issue_setup_link", e.note)
            self.assertNotIn(token, f"{e.changes}{e.note}")

    def test_refuses_without_changing_anything(self):
        protected = User.objects.create_user("kd", password="x", company=self.co, role="md", is_staff=True)
        rooted = User.objects.create_user("rt", password="x", company=self.co, role="md", is_superuser=True)
        gone = User.objects.create_user("gone@acme.test", email="gone@acme.test", password="x", company=self.co,
                                        role="md", is_active=False)
        attempts = [dict(email="nobody@acme.test"), dict(username="nobody"), dict(email="mo@acme.test"),
                    dict(username="kd"), dict(username="rt"), dict(email="gone@acme.test")]
        snapshot = {u.pk: (u.password, u.link_serial) for u in User.objects.all()}
        before = AccountEvent.objects.count()
        for attempt in attempts:
            with self.assertRaises(CommandError, msg=str(attempt)):
                run("issue_setup_link", **attempt)
        self.assertEqual({u.pk: (u.password, u.link_serial) for u in User.objects.all()}, snapshot)
        self.assertEqual(AccountEvent.objects.count(), before)

    def test_an_email_matching_two_accounts_is_refused(self):
        # The database now refuses two accounts with one email, so this can only come from old data. Simulate it.
        twins = [User(pk=9001, username="twin1", role="md"), User(pk=9002, username="twin2", role="md")]
        before = AccountEvent.objects.count()
        with patch("tracker.management.commands.issue_setup_link.User.objects.filter", return_value=twins):
            with self.assertRaises(CommandError) as ctx:
                run("issue_setup_link", email="twin@acme.test")
        self.assertIn("More than one account", str(ctx.exception))
        self.assertEqual(AccountEvent.objects.count(), before)

    def test_email_and_username_cannot_both_be_given_and_one_is_needed(self):
        with self.assertRaises(CommandError):
            run("issue_setup_link", email="ada@acme.test", username="x")
        with self.assertRaises(CommandError):
            run("issue_setup_link")

    @override_settings(EMAIL_DELIVERY_ENABLED=True)
    def test_email_enabled_sends_without_printing(self):
        out = run("issue_setup_link", email="ada@acme.test")
        self.assertNotIn("#token", out)
        self.assertEqual(len(mail.outbox), 1)

    @override_settings(EMAIL_DELIVERY_ENABLED=True)
    def test_email_failure_needs_the_explicit_flag_to_show_the_link(self):
        with patch("tracker.account_services.send_mail", side_effect=OSError("down")), \
                self.assertLogs("tracker.account_services", "WARNING"):
            with self.assertRaises(CommandError) as ctx:
                run("issue_setup_link", email="ada@acme.test")
            self.assertNotIn("#token", str(ctx.exception))
            out = run("issue_setup_link", email="ada@acme.test", show_link_if_email_fails=True)
        self.assertIn("#token=", out)
