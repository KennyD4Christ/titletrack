"""Django admin company onboarding (Stage 3): who may use it, the workflow, and how the one-time link is handled.

Derived from the implemented code: CompanyAdmin in tracker/admin.py (routes admin:tracker_company_onboard and
admin:tracker_company_resend_setup_link, the OnboardCompanyForm / ResendSetupLinkForm fields, the
"resend_setup_link" changelist action), tracker/onboarding_services.py, and
templates/admin/tracker/company/onboard.html.

Self-contained: it imports nothing from test_company_onboarding.py (the Stage 1/2 service, command and permission
tests live there). Email is always mocked or disabled; nothing talks to a live service.
"""
import html
import logging
import re
import smtplib
from unittest.mock import patch

from django.contrib.admin.models import ADDITION, CHANGE, LogEntry
from django.contrib.auth.models import AnonymousUser, Group, Permission
from django.core.exceptions import PermissionDenied
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from . import account_services as acc
from . import onboarding_services as svc
from .models import AccountEvent, Company, Department, User

SITE = "https://app.example.test"
STRONG_PASSWORD = "Kestrel-Quartz-Lantern-4817!"
FORM = {"company": "Acme Estates", "departments": "Legal\nSurvey\n\n", "md_email": "Ada@Acme.Test",
        "md_first_name": "Ada", "md_last_name": "Obi"}
SERVICE_INPUT = dict(company="Acme Estates", departments=["Legal", "Survey"], md_email="ada@acme.test",
                     md_first_name="Ada", md_last_name="Obi")


# ---------- helpers ----------

def onboard_url():
    return reverse("admin:tracker_company_onboard")


def resend_url(company_or_pk):
    pk = getattr(company_or_pk, "pk", company_or_pk)
    return reverse("admin:tracker_company_resend_setup_link", args=[pk])


def changelist_url():
    return reverse("admin:tracker_company_changelist")


def operator_permission():
    return Permission.objects.get(content_type__app_label="tracker", codename="onboard_company")


def make_user(username, *, staff=True, superuser=False, active=True, company=None, role=None, grant=None,
              extra_perms=()):
    """grant: None, "direct" (the permission assigned to the user) or "group" (only through a group)."""
    extra = {"role": role} if role else {}
    user = User.objects.create_user(username, password="x", company=company, is_staff=staff, is_active=active,
                                    is_superuser=superuser, **extra)
    if grant == "direct":
        user.user_permissions.add(operator_permission())
    elif grant == "group":
        group = Group.objects.create(name=f"group-{username}")
        group.permissions.add(operator_permission())
        user.groups.add(group)
    for codename in extra_perms:
        user.user_permissions.add(Permission.objects.get(content_type__app_label="tracker", codename=codename))
    return user


def token_in(response):
    """The one-time token if the response contains a setup link, else None."""
    found = re.search(r"#token=([^\"<\s&]+)", response.content.decode())
    return found.group(1) if found else None


def page_text(response):
    """The visible text of a page with tags removed, entities decoded and whitespace collapsed."""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", response.content.decode())).split())


def counts():
    return (Company.objects.count(), Department.objects.count(), User.objects.count(),
            AccountEvent.objects.count(), LogEntry.objects.count())


def issued_count():
    return AccountEvent.objects.filter(action="setup_link_issued").count()


def everything_stored():
    """Every audit row, as text: the AccountEvent history and Django's admin history."""
    return str(list(AccountEvent.objects.values())) + str(list(LogEntry.objects.values()))


class _Collect(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)


class LogCapture:
    """Collects everything logged while it is active, whatever the project's logging setup."""
    NAMES = ("", "tracker", "tracker.onboarding_services", "tracker.account_services", "django.request",
             "django.security")

    def __enter__(self):
        self.handler = _Collect()
        self._saved = []
        for name in self.NAMES:
            logger = logging.getLogger(name)
            self._saved.append((logger, logger.level))
            logger.setLevel(logging.DEBUG)
            logger.addHandler(self.handler)
        return self

    def __exit__(self, *exc):
        for logger, level in self._saved:
            logger.removeHandler(self.handler)
            logger.setLevel(level)

    @property
    def records(self):
        return self.handler.records

    @property
    def text(self):
        formatter = logging.Formatter()
        return "\n".join(formatter.format(r) for r in self.records)


def onboard_through_service(operator, **change):
    """Create a company the supported way and return (result, token). Delivery must be disabled or mocked."""
    result, outcome = svc.onboard_as_operator(operator, **{**SERVICE_INPUT, **change})
    token = outcome.link.split("#token=")[1] if outcome.link else None
    return result, token


# ---------- 1. who counts as a platform operator ----------

class OperatorDesignationTests(TestCase):
    """is_platform_operator: only an active staff user with tracker.onboard_company assigned directly."""

    def test_designation_matrix(self):
        co = Company.objects.create(name="Acme")
        cases = [
            ("direct permission, staff, no company", make_user("a", grant="direct"), True),
            ("direct permission, staff, attached to a company", make_user("b", grant="direct", company=co), True),
            ("direct permission, superuser", make_user("c", superuser=True, grant="direct"), True),
            ("superuser WITHOUT the permission", make_user("d", superuser=True), False),
            ("superuser attached to a company, WITHOUT the permission", make_user("e", superuser=True, company=co), False),
            ("staff WITHOUT the permission", make_user("f"), False),
            ("permission only through a group", make_user("g", grant="group"), False),
            ("superuser, permission only through a group", make_user("g2", superuser=True, grant="group"), False),
            ("permission assigned but not staff", make_user("h", staff=False, grant="direct", company=co, role="md"), False),
            ("permission assigned but inactive", make_user("i", active=False, grant="direct"), False),
            ("MD role alone", make_user("j", staff=False, company=co, role="md"), False),
        ]
        for label, user, expected in cases:
            self.assertIs(svc.is_platform_operator(user), expected, label)

    def test_anonymous_and_none_are_not_operators(self):
        self.assertFalse(svc.is_platform_operator(AnonymousUser()))
        self.assertFalse(svc.is_platform_operator(None))

    def test_the_answer_follows_the_database_not_a_cache(self):
        user = make_user("op", grant="direct")
        self.assertTrue(svc.is_platform_operator(user))
        user.user_permissions.remove(operator_permission())
        self.assertFalse(svc.is_platform_operator(user))           # same Python object
        user.user_permissions.add(operator_permission())
        self.assertTrue(svc.is_platform_operator(user))
        User.objects.filter(pk=user.pk).update(is_active=False)
        user.refresh_from_db()
        self.assertFalse(svc.is_platform_operator(user))

    def test_the_service_functions_refuse_everyone_else_and_write_nothing(self):
        company = Company.objects.create(name="Existing")
        for user in (make_user("plain"), make_user("root", superuser=True), make_user("viaGroup", grant="group")):
            before = counts()
            with self.assertRaises(PermissionDenied):
                svc.onboard_as_operator(user, **SERVICE_INPUT)
            with self.assertRaises(PermissionDenied):
                svc.reissue_md_setup_link(user, company.pk)
            self.assertEqual(counts(), before, user.username)


# ---------- 2-4. access to the admin pages ----------

@override_settings(EMAIL_DELIVERY_ENABLED=False, SITE_URL=SITE)
class AccessControlTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Existing")

    def urls(self):
        return (onboard_url(), resend_url(self.company))

    def test_an_authorised_operator_can_open_the_onboarding_page(self):
        self.client.force_login(make_user("op", grant="direct"))
        response = self.client.get(onboard_url())
        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertEqual(list(form.fields), ["company", "departments", "md_email", "md_first_name", "md_last_name",
                                             "reveal_if_email_fails"])
        self.assertContains(response, "Onboard company")
        self.assertIn("no-store", response["Cache-Control"])

    def test_a_superuser_who_is_designated_is_let_in_like_anyone_else(self):
        self.client.force_login(make_user("root", superuser=True, grant="direct"))
        self.assertEqual(self.client.get(onboard_url()).status_code, 200)

    def test_unauthenticated_users_are_sent_to_the_admin_login(self):
        for url in self.urls():
            for method in (self.client.get, self.client.post):
                response = method(url)
                self.assertEqual(response.status_code, 302, url)
                self.assertIn("/admin/login/", response["Location"])
        self.assertEqual(counts()[0], 1)

    def test_a_non_staff_user_is_not_let_in_even_with_the_permission(self):
        md = make_user("md", staff=False, grant="direct", company=self.company, role="md")
        self.client.force_login(md)
        for url in self.urls():
            self.assertEqual(self.client.get(url).status_code, 302)
            self.assertEqual(self.client.post(url, FORM).status_code, 302)
        self.assertEqual(Company.objects.count(), 1)

    def test_an_inactive_user_is_not_let_in_even_with_the_permission(self):
        self.client.force_login(make_user("gone", active=False, grant="direct"))
        for url in self.urls():
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302)
            self.assertIn("/admin/login/", response["Location"])

    def test_staff_superuser_and_group_membership_do_not_grant_access(self):
        users = {
            "plain staff": make_user("plain"),
            "superuser without the permission": make_user("root", superuser=True),
            "permission only through a group": make_user("viaGroup", grant="group"),
            "superuser, permission only through a group": make_user("rootGroup", superuser=True, grant="group"),
        }
        for label, user in users.items():
            self.client.force_login(user)
            before = counts()
            for url in self.urls():
                self.assertEqual(self.client.get(url).status_code, 403, f"{label}: GET {url}")
                self.assertEqual(self.client.post(url, FORM).status_code, 403, f"{label}: POST {url}")
            self.assertEqual(counts(), before, label)

    def test_the_resend_page_does_not_reveal_whether_a_company_exists_to_non_operators(self):
        self.client.force_login(make_user("plain"))
        self.assertEqual(self.client.get(resend_url(999999)).status_code, 403)
        self.assertEqual(self.client.get(resend_url(self.company)).status_code, 403)

    def test_an_operator_gets_a_404_for_an_unknown_company(self):
        self.client.force_login(make_user("op", grant="direct"))
        self.assertEqual(self.client.get(resend_url(999999)).status_code, 404)
        self.assertEqual(self.client.post(resend_url(999999), {}).status_code, 404)

    def test_removing_the_permission_closes_every_page_on_the_next_request(self):
        op = make_user("op", grant="direct")
        self.client.force_login(op)
        self.assertEqual(self.client.get(onboard_url()).status_code, 200)
        op.user_permissions.remove(operator_permission())
        for url in self.urls():
            self.assertEqual(self.client.get(url).status_code, 403)

    def test_posts_without_a_csrf_token_are_refused(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(make_user("op", grant="direct"))
        before = counts()
        self.assertEqual(client.post(onboard_url(), FORM).status_code, 403)
        self.assertEqual(counts(), before)
        page = client.get(onboard_url())
        csrf = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page.content.decode()).group(1)
        self.assertEqual(client.post(onboard_url(), {**FORM, "csrfmiddlewaretoken": csrf}).status_code, 200)
        self.assertTrue(Company.objects.filter(name="Acme Estates").exists())


# ---------- 5-6. the onboarding POST ----------

@override_settings(EMAIL_DELIVERY_ENABLED=False, SITE_URL=SITE)
class OnboardingPostTests(TestCase):
    def setUp(self):
        self.op = make_user("op", grant="direct")
        self.client.force_login(self.op)

    def test_a_valid_post_creates_the_company_departments_and_first_md(self):
        response = self.client.post(onboard_url(), FORM)
        self.assertEqual(response.status_code, 200)
        company = Company.objects.get(name="Acme Estates")
        self.assertEqual(sorted(company.departments.values_list("name", flat=True)), ["Legal", "Survey"])
        md = User.objects.get(company=company)
        self.assertEqual((md.username, md.email, md.first_name, md.last_name, md.role),
                         ("ada@acme.test", "ada@acme.test", "Ada", "Obi", "md"))
        self.assertFalse(md.has_usable_password())
        self.assertFalse(md.is_staff or md.is_superuser)
        self.assertTrue(md.is_active)
        self.assertEqual(Company.objects.count(), 1)
        self.assertEqual(User.objects.filter(company=company).count(), 1)
        self.assertIn("Company “Acme Estates” was created with 2 departments and the MD account ada@acme.test.",
                      page_text(response))

    def test_the_audit_trail_names_the_operator_where_the_company_rule_allows(self):
        self.client.post(onboard_url(), FORM)
        events = {e.action: e for e in AccountEvent.objects.all()}
        self.assertEqual(sorted(events), ["created", "link_revealed", "setup_link_issued"])
        self.assertEqual((events["created"].actor, events["created"].note),
                         (self.op, "Created by platform administration"))
        self.assertEqual((events["setup_link_issued"].actor, events["setup_link_issued"].note),
                         (None, "Issued by platform administration"))      # issue_link refuses an actor from another company
        self.assertEqual(events["link_revealed"].actor, self.op)
        entry = LogEntry.objects.get(action_flag=ADDITION)
        self.assertEqual((entry.user, entry.object_repr), (self.op, "Acme Estates"))
        self.assertIn("setup link: revealed", entry.change_message)

    def test_departments_are_optional_and_blank_lines_are_ignored(self):
        self.client.post(onboard_url(), {**FORM, "departments": "\n  \nLegal \n\n"})
        self.assertEqual(list(Company.objects.get(name="Acme Estates").departments.values_list("name", flat=True)),
                         ["Legal"])
        response = self.client.post(onboard_url(), {**FORM, "company": "Beta Homes", "md_email": "b@beta.test",
                                                    "departments": ""})
        self.assertEqual(Company.objects.get(name="Beta Homes").departments.count(), 0)
        self.assertIn("with 0 departments", page_text(response))

    def test_get_and_a_refused_post_create_nothing(self):
        before = counts()
        self.client.get(onboard_url())
        self.client.post(onboard_url(), {**FORM, "md_email": "not-an-email"})
        self.assertEqual(counts(), before)

    def test_every_rule_is_shown_beside_its_field_and_nothing_is_partly_created(self):
        existing = Company.objects.create(name="Real   Estates")
        User.objects.create_user("taken@x.test", email="Taken@X.test", company=existing)
        cases = [
            ({"company": "real estates"}, "company", "already exists"),
            ({"company": "  REAL   ESTATES "}, "company", "already exists"),
            ({"company": "Demo Homes"}, "company", "reserved"),
            ({"company": " "}, "company", "required"),
            ({"company": "x" * 201}, "company", "too long"),
            ({"md_email": "nope"}, "md_email", "not valid"),
            ({"md_email": ""}, "md_email", "not valid"),
            ({"md_email": "TAKEN@x.test"}, "md_email", "already used"),
            ({"md_first_name": " "}, "md_first_name", "required"),
            ({"md_last_name": ""}, "md_last_name", "required"),
            ({"departments": "Legal\nlegal"}, "departments", "listed twice"),
            ({"departments": "y" * 101}, "departments", "too long"),
        ]
        for change, field, text in cases:
            before = counts()
            response = self.client.post(onboard_url(), {**FORM, **change})
            self.assertEqual(response.status_code, 200, str(change))
            errors = response.context["form"].errors
            self.assertEqual(list(errors), [field], str(change))
            self.assertIn(text, " ".join(errors[field]), str(change))
            self.assertIsNone(token_in(response), str(change))
            self.assertIn("no-store", response["Cache-Control"])
            self.assertEqual(counts(), before, str(change))

    def test_what_was_typed_is_kept_when_a_rule_is_broken(self):
        response = self.client.post(onboard_url(), {**FORM, "md_email": "nope"})
        self.assertContains(response, 'value="Acme Estates"')
        self.assertContains(response, 'value="Ada"')
        self.assertContains(response, "Legal")

    def test_a_failure_part_way_leaves_nothing_behind(self):
        self.client.raise_request_exception = False
        before = counts()
        with patch("tracker.account_services.issue_link", side_effect=RuntimeError("boom")):
            response = self.client.post(onboard_url(), FORM)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(counts(), before)

    def test_submitting_the_same_company_twice_creates_only_one(self):
        self.client.post(onboard_url(), FORM)
        before = counts()
        response = self.client.post(onboard_url(), FORM)
        self.assertEqual(response.status_code, 200)
        self.assertIn("company", response.context["form"].errors)
        self.assertEqual(counts(), before)
        self.assertEqual(Company.objects.filter(name="Acme Estates").count(), 1)
        self.assertEqual(User.objects.filter(email="ada@acme.test").count(), 1)

    def test_names_are_escaped_on_the_result_and_error_pages(self):
        response = self.client.post(onboard_url(), {**FORM, "company": "Acme <b>Estates</b>"})
        self.assertNotContains(response, "<b>Estates</b>")
        self.assertContains(response, "&lt;b&gt;Estates&lt;/b&gt;")
        response = self.client.post(onboard_url(), {**FORM, "company": "Acme <b>Estates</b>", "md_email": "b@x.test"})
        self.assertNotContains(response, "<b>Estates</b>")                    # already exists: error page, still escaped


# ---------- 7-8, 11. how the one-time link is shown and kept ----------

@override_settings(EMAIL_DELIVERY_ENABLED=False, SITE_URL=SITE)
class LinkDisplayTests(TestCase):
    def setUp(self):
        self.op = make_user("op", grant="direct")
        self.client.force_login(self.op)

    def test_with_email_delivery_off_the_link_is_shown_once_and_works_once(self):
        response = self.client.post(onboard_url(), FORM)
        token = token_in(response)
        self.assertIsNotNone(token)
        self.assertContains(response, f'value="{SITE}/set-password#token={token}"')
        self.assertIn("Setup link (shown once)", page_text(response))
        self.assertIn("Works once and expires on", page_text(response))
        self.assertEqual(acc.complete_password_link(token, acc.SETUP, STRONG_PASSWORD).email, "ada@acme.test")
        with self.assertRaises(acc.InvalidLink):
            acc.complete_password_link(token, acc.SETUP, STRONG_PASSWORD)

    def test_reloading_or_revisiting_never_shows_the_link_again(self):
        response = self.client.post(onboard_url(), FORM)
        token = token_in(response)
        for page in (self.client.get(onboard_url()),
                     self.client.get(resend_url(Company.objects.get(name="Acme Estates")))):
            self.assertNotIn(token, page.content.decode())

    def test_the_link_is_not_kept_in_the_session_cookies_audit_rows_or_logs(self):
        with LogCapture() as logs:
            response = self.client.post(onboard_url(), FORM)
        token = token_in(response)
        self.assertIn("no-store", response["Cache-Control"])
        self.assertIn("no-cache", response.get("Pragma", ""))
        self.assertNotIn(token, str(dict(self.client.session)))                 # not in the session
        self.assertNotIn(token, self.client.cookies.output())                   # not in a cookie (messages use them)
        self.assertNotIn(token, response.cookies.output())
        self.assertNotIn(token, everything_stored())                            # not in AccountEvent or LogEntry
        self.assertTrue(any(r.name == "tracker.onboarding_services" for r in logs.records))     # logging was live
        self.assertNotIn(token, logs.text)
        self.assertNotIn(f"{SITE}/set-password", logs.text)

    def test_the_audit_rows_say_the_link_was_shown_but_never_contain_it(self):
        self.client.post(onboard_url(), FORM)
        revealed = AccountEvent.objects.get(action="link_revealed")
        self.assertEqual(revealed.note, "Email delivery is disabled: link shown once to the administrator.")

    @override_settings(EMAIL_DELIVERY_ENABLED=True)
    def test_when_the_email_is_sent_no_link_is_shown_but_it_is_in_the_email(self):
        with patch("tracker.account_services.send_mail") as send:
            response = self.client.post(onboard_url(), FORM)
        self.assertEqual(send.call_count, 1)
        recipients, body = send.call_args.args[3], send.call_args.args[1]
        self.assertEqual(recipients, ["ada@acme.test"])
        self.assertIn(f"{SITE}/set-password#token=", body)
        self.assertIsNone(token_in(response))
        self.assertIn("The setup link was emailed to ada@acme.test.", page_text(response))
        self.assertEqual(AccountEvent.objects.filter(action__in=("link_revealed", "email_failed")).count(), 0)

    @override_settings(EMAIL_DELIVERY_ENABLED=True)
    def test_asking_to_reveal_the_link_changes_nothing_when_the_email_was_sent(self):
        with patch("tracker.account_services.send_mail"):
            response = self.client.post(onboard_url(), {**FORM, "reveal_if_email_fails": "on"})
        self.assertIsNone(token_in(response))
        self.assertEqual(AccountEvent.objects.filter(action="link_revealed").count(), 0)

    @override_settings(EMAIL_DELIVERY_ENABLED=True)
    def test_when_the_email_fails_the_account_stays_and_no_link_is_shown(self):
        with LogCapture() as logs, patch("tracker.account_services.send_mail",
                                         side_effect=smtplib.SMTPException("secret-host-detail")):
            response = self.client.post(onboard_url(), FORM)
        company = Company.objects.get(name="Acme Estates")
        text = page_text(response)
        self.assertIn("could not be sent", text)
        self.assertIn("nothing needs to be entered again", text)
        self.assertIsNone(token_in(response))
        self.assertContains(response, f'href="{resend_url(company)}"')          # the way to recover
        self.assertEqual(AccountEvent.objects.get(action="email_failed").actor, self.op)
        self.assertEqual(AccountEvent.objects.filter(action="link_revealed").count(), 0)
        self.assertEqual(User.objects.filter(company=company, role="md").count(), 1)
        self.assertIn("SMTPException", logs.text)                               # the type is logged...
        self.assertNotIn("secret-host-detail", logs.text)                       # ...not the message
        self.assertNotIn("ada@acme.test", logs.text)

    @override_settings(EMAIL_DELIVERY_ENABLED=True)
    def test_when_the_email_fails_the_link_is_shown_only_if_the_operator_asked(self):
        with patch("tracker.account_services.send_mail", side_effect=smtplib.SMTPException("down")):
            response = self.client.post(onboard_url(), {**FORM, "reveal_if_email_fails": "on"})
        token = token_in(response)
        self.assertIsNotNone(token)
        self.assertEqual(AccountEvent.objects.get(action="link_revealed").actor, self.op)
        self.assertNotIn(token, everything_stored())

    def test_unauthorised_users_never_get_a_page_that_could_hold_a_link(self):
        for user in (make_user("plain"), make_user("root", superuser=True), make_user("viaGroup", grant="group")):
            self.client.force_login(user)
            before = counts()
            response = self.client.post(onboard_url(), FORM)
            self.assertEqual(response.status_code, 403)
            self.assertIsNone(token_in(response))
            self.assertNotIn("/set-password", response.content.decode())
            self.assertEqual(counts(), before)

    def test_every_operator_page_carries_no_cache_headers(self):
        result, _ = onboard_through_service(self.op)
        pages = {
            "form": self.client.get(onboard_url()),
            "refused form": self.client.post(onboard_url(), {**FORM, "md_email": "nope"}),
            "created": self.client.post(onboard_url(), {**FORM, "company": "Beta", "md_email": "b@beta.test"}),
            "confirm": self.client.get(resend_url(result.company)),
            "reissued": self.client.post(resend_url(result.company), {}),
            "refused resend": self.client.post(resend_url(Company.objects.create(name="Bare")), {}),
        }
        for label, response in pages.items():
            self.assertEqual(response.status_code, 200, label)
            self.assertIn("no-store", response["Cache-Control"], label)
            self.assertIn("no-cache", response.get("Pragma", ""), label)


# ---------- 9-10. reissuing a setup link ----------

@override_settings(EMAIL_DELIVERY_ENABLED=False, SITE_URL=SITE)
class ResendTests(TestCase):
    def setUp(self):
        self.op = make_user("op", grant="direct")
        self.client.force_login(self.op)
        self.result, self.first_token = onboard_through_service(self.op)
        self.company, self.md = self.result.company, self.result.md
        self.url = resend_url(self.company)

    def test_the_confirmation_page_explains_and_changes_nothing(self):
        before = counts()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        text = page_text(response)
        self.assertIn("This issues a new setup link for ada@acme.test (Acme Estates).", text)
        self.assertIn("Any link already sent to them stops working.", text)
        self.assertIn("no-store", response["Cache-Control"])
        self.assertIsNone(token_in(response))                                   # a GET never carries a link
        self.assertNotIn("/set-password", response.content.decode())
        self.assertEqual(counts(), before)
        self.assertEqual(list(response.context["form"].fields), ["reveal_if_email_fails"])

    def test_posting_issues_a_new_link_and_the_old_one_stops_working(self):
        response = self.client.post(self.url, {})
        new = token_in(response)
        self.assertIsNotNone(new)
        self.assertNotEqual(new, self.first_token)
        self.assertIn("A new setup link was issued for ada@acme.test (Acme Estates).", page_text(response))
        self.assertIn("Any earlier link no longer works.", page_text(response))
        with self.assertRaises(acc.InvalidLink):
            acc.complete_password_link(self.first_token, acc.SETUP, STRONG_PASSWORD)
        self.assertEqual(acc.complete_password_link(new, acc.SETUP, STRONG_PASSWORD).pk, self.md.pk)

    def test_a_reissue_is_audited_without_the_link(self):
        before = issued_count()
        response = self.client.post(self.url, {})
        new = token_in(response)
        self.assertEqual(issued_count(), before + 1)
        latest = AccountEvent.objects.filter(action="setup_link_issued").latest("id")
        self.assertEqual((latest.note, latest.actor), ("Issued by platform administration (new link)", None))
        self.assertEqual(AccountEvent.objects.filter(action="link_revealed").latest("id").actor, self.op)
        entry = LogEntry.objects.get(action_flag=CHANGE, user=self.op)
        self.assertEqual(entry.object_repr, "Acme Estates")
        self.assertIn("setup link: revealed", entry.change_message)
        self.assertNotIn(new, everything_stored())

    def test_the_reissued_link_is_not_kept_in_the_session_cookies_or_logs(self):
        with LogCapture() as logs:
            response = self.client.post(self.url, {})
        new = token_in(response)
        self.assertIn("no-store", response["Cache-Control"])
        self.assertNotIn(new, str(dict(self.client.session)))
        self.assertNotIn(new, self.client.cookies.output())
        self.assertNotIn(new, logs.text)
        self.assertTrue(any("new setup link" in r.getMessage() for r in logs.records))
        self.assertNotIn(new, self.client.get(self.url).content.decode())

    def test_only_that_companys_md_is_affected(self):
        other, other_token = onboard_through_service(self.op, company="Beta Homes", md_email="b@beta.test",
                                                      md_first_name="Bola", md_last_name="Eze")
        self.client.post(self.url, {})
        self.assertEqual(acc.complete_password_link(other_token, acc.SETUP, STRONG_PASSWORD).pk, other.md.pk)

    def test_it_is_refused_once_the_md_has_set_a_password(self):
        acc.complete_password_link(self.first_token, acc.SETUP, STRONG_PASSWORD)
        before = counts()
        for method in (self.client.get, self.client.post):
            response = method(self.url)
            self.assertEqual(response.status_code, 200)
            self.assertIn("nothing to resend", page_text(response))
            self.assertIsNone(token_in(response))
        self.assertEqual(counts(), before)

    def test_it_is_refused_for_accounts_that_must_not_receive_a_link(self):
        cases = [
            ("inactive MD", dict(is_active=False)),
            ("MD who is a Django admin user", dict(is_staff=True)),
            ("MD who is a superuser", dict(is_superuser=True)),
            ("not an MD", dict(role="manager")),
        ]
        for label, change in cases:
            User.objects.filter(pk=self.md.pk).update(is_active=True, is_staff=False, is_superuser=False, role="md")
            User.objects.filter(pk=self.md.pk).update(**change)
            before = counts()
            response = self.client.post(self.url, {})
            self.assertIn("nothing to resend", page_text(response), label)
            self.assertIsNone(token_in(response), label)
            self.assertEqual(counts(), before, label)

    def test_it_is_refused_when_two_mds_are_waiting(self):
        other = User(username="second@acme.test", email="second@acme.test", company=self.company, role="md")
        other.set_unusable_password()
        other.save()
        before = counts()
        response = self.client.post(self.url, {})
        self.assertIn("More than one MD", page_text(response))
        self.assertEqual(counts(), before)

    def test_a_company_with_no_md_has_nothing_to_resend(self):
        bare = Company.objects.create(name="Bare")
        response = self.client.post(resend_url(bare), {})
        self.assertIn("nothing to resend", page_text(response))
        self.assertEqual(issued_count(), 1)

    def test_an_md_who_sets_a_password_during_the_request_is_not_given_a_link(self):
        acc.complete_password_link(self.first_token, acc.SETUP, STRONG_PASSWORD)
        stale = User.objects.get(pk=self.md.pk)
        before = counts()
        with patch("tracker.onboarding_services.waiting_md", return_value=stale):      # the view looked a moment too early
            response = self.client.post(self.url, {})
        self.assertIn("no longer waiting to set a password", page_text(response))
        self.assertIsNone(token_in(response))
        self.assertEqual(counts(), before)

    def test_an_account_that_stops_being_an_md_during_the_request_is_not_given_a_link(self):
        stale = User.objects.get(pk=self.md.pk)
        User.objects.filter(pk=self.md.pk).update(role="manager")
        before = counts()

        with patch("tracker.onboarding_services.waiting_md", return_value=stale):
            response = self.client.post(self.url, {})

        self.assertIn("no longer waiting to set a password", page_text(response))
        self.assertIsNone(token_in(response))
        self.assertEqual(counts(), before)

    def test_refusals_show_a_way_back_and_no_link(self):
        bare = Company.objects.create(name="Bare")
        response = self.client.get(resend_url(bare))
        self.assertContains(response, f'href="{changelist_url()}"')
        self.assertIn("no-store", response["Cache-Control"])
        self.assertNotIn("/set-password", response.content.decode())

    def test_non_operators_cannot_reissue_and_no_link_is_issued(self):
        for user in (make_user("plain"), make_user("root", superuser=True), make_user("viaGroup", grant="group")):
            self.client.force_login(user)
            before = counts()
            for method in (self.client.get, self.client.post):
                response = method(self.url)
                self.assertEqual(response.status_code, 403, user.username)
                self.assertIsNone(token_in(response))
            self.assertEqual(counts(), before, user.username)
        # nothing was reissued, so the original link is still the valid one
        self.assertEqual(acc.complete_password_link(self.first_token, acc.SETUP, STRONG_PASSWORD).pk, self.md.pk)

    @override_settings(EMAIL_DELIVERY_ENABLED=True)
    def test_when_the_reissue_email_is_sent_no_link_is_shown(self):
        with patch("tracker.account_services.send_mail") as send:
            response = self.client.post(self.url, {"reveal_if_email_fails": "on"})
        self.assertEqual(send.call_count, 1)
        self.assertIsNone(token_in(response))
        self.assertIn("The setup link was emailed to ada@acme.test.", page_text(response))

    @override_settings(EMAIL_DELIVERY_ENABLED=True)
    def test_when_the_reissue_email_fails_the_link_is_shown_only_if_asked(self):
        with patch("tracker.account_services.send_mail", side_effect=smtplib.SMTPException("down")):
            plain = self.client.post(self.url, {})
            asked = self.client.post(self.url, {"reveal_if_email_fails": "on"})
        self.assertIsNone(token_in(plain))
        self.assertIn("could not be sent", page_text(plain))
        self.assertIsNotNone(token_in(asked))
        self.assertEqual(AccountEvent.objects.filter(action="email_failed").count(), 2)


# ---------- 12. the changelist action ----------

@override_settings(EMAIL_DELIVERY_ENABLED=False, SITE_URL=SITE)
class ChangelistActionTests(TestCase):
    def setUp(self):
        self.op = make_user("op", superuser=True, grant="direct")          # a superuser sees the changelist
        self.result, self.token = onboard_through_service(self.op)
        self.company = self.result.company
        self.other = Company.objects.create(name="Second")

    def run_action(self, user, selected, follow=False):
        self.client.force_login(user)
        return self.client.post(changelist_url(), {"action": "resend_setup_link", "_selected_action": selected},
                                follow=follow)

    def assert_stays_on_the_changelist(self, response, label=""):
        """Django answers a refused action by redirecting back or by re-rendering the list; either way the user
        ends on the changelist, never on the resend page, and sees no MD details."""
        final = response.redirect_chain[-1][0] if response.redirect_chain else changelist_url()
        self.assertEqual(final, changelist_url(), label)
        self.assertEqual(response.status_code, 200, label)
        self.assertNotIn("ada@acme.test", page_text(response), label)
        self.assertNotIn("issues a new setup link", page_text(response), label)

    def test_one_company_leads_to_the_resend_confirmation_and_issues_nothing_itself(self):
        before = counts()
        response = self.run_action(self.op, [self.company.pk])
        self.assertRedirects(response, resend_url(self.company), fetch_redirect_response=False)
        self.assertEqual(counts(), before)                                  # the action only redirects
        self.assertEqual(acc.complete_password_link(self.token, acc.SETUP, STRONG_PASSWORD).pk, self.result.md.pk)

    def test_a_designated_staff_user_with_view_permission_can_use_it(self):
        staff = make_user("viewer", grant="direct", extra_perms=["view_company"])
        response = self.run_action(staff, [self.company.pk])
        self.assertRedirects(response, resend_url(self.company), fetch_redirect_response=False)

    def test_selecting_two_companies_is_refused_with_a_message(self):
        before = counts()
        response = self.run_action(self.op, [self.company.pk, self.other.pk], follow=True)
        self.assert_stays_on_the_changelist(response)
        self.assertIn("Select exactly one company.", page_text(response))
        self.assertEqual(counts(), before)

    def test_selecting_nothing_does_not_reach_the_resend_page(self):
        before = counts()
        response = self.run_action(self.op, [], follow=True)
        self.assert_stays_on_the_changelist(response)
        self.assertEqual(counts(), before)

    def test_it_is_not_offered_to_a_superuser_who_is_not_designated(self):
        root = make_user("root", superuser=True)
        before = counts()
        response = self.run_action(root, [self.company.pk], follow=True)
        self.assert_stays_on_the_changelist(response)
        self.assertEqual(counts(), before)

    def test_it_is_not_offered_to_staff_with_view_permission_but_no_designation(self):
        for user in (make_user("viewer", extra_perms=["view_company"]),
                     make_user("viaGroup", grant="group", extra_perms=["view_company"])):
            response = self.run_action(user, [self.company.pk], follow=True)
            self.assert_stays_on_the_changelist(response, user.username)

    def test_the_action_is_listed_only_for_operators_and_the_default_actions_remain(self):
        self.client.force_login(self.op)
        listed = self.client.get(changelist_url()).content.decode()
        self.assertIn('value="resend_setup_link"', listed)
        self.assertIn('value="delete_selected"', listed)
        self.client.force_login(make_user("root", superuser=True))
        listed = self.client.get(changelist_url()).content.decode()
        self.assertNotIn('value="resend_setup_link"', listed)
        self.assertIn('value="delete_selected"', listed)

    def test_a_designated_user_without_company_permissions_cannot_see_the_changelist_but_can_onboard(self):
        staff = make_user("narrow", grant="direct")
        self.client.force_login(staff)
        self.assertEqual(self.client.get(changelist_url()).status_code, 403)
        self.assertEqual(self.client.get(onboard_url()).status_code, 200)   # the page is reached by its own URL

    def test_a_non_staff_company_user_cannot_use_the_action(self):
        md = make_user("md", staff=False, grant="direct", company=self.company, role="md")
        response = self.run_action(md, [self.company.pk])
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response["Location"])
