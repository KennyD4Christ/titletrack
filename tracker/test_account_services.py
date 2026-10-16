import json
import re
import threading
import time
from datetime import timedelta
from smtplib import SMTPException
from unittest import skipUnless
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import authenticate
from django.core import mail
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.test import RequestFactory, TestCase, TransactionTestCase, override_settings

from . import account_services as acc
from .models import AccountEvent, Company, User

STRONG = "Tr1cky-Horse-Battery-42"
DAY = 24 * 3600


def later(seconds):
    """Pretend the clock is `seconds` ahead (signed tokens read time.time())."""
    return patch("django.core.signing.time.time", return_value=time.time() + seconds)


def token_of(url):
    return url.split("#token=", 1)[1]


class Base(TestCase):
    def setUp(self):
        self.co = Company.objects.create(name="Acme Estates")
        self.other = Company.objects.create(name="Other Estates")
        self.md = User.objects.create_user("ada@acme.test", email="ada@acme.test", company=self.co, role="md",
                                           first_name="Ada", last_name="Obi")
        self.md.set_unusable_password()
        self.md.save()
        self.staff = User.objects.create_user("sam@acme.test", email="sam@acme.test", password="x",
                                              company=self.co, role="staff")
        self.eve = User.objects.create_user("eve@other.test", email="eve@other.test", password="x",
                                            company=self.other, role="md")

    def fresh(self, user):
        return User.objects.get(pk=user.pk)

    def assertInvalid(self, token, purpose=acc.SETUP):
        with self.assertRaises(acc.InvalidLink):
            acc.complete_password_link(token, purpose, STRONG)


class AuditModelTests(Base):
    def test_events_cannot_be_edited_or_deleted(self):
        event = acc.log_account_event(self.co.pk, AccountEvent.Action.CREATED, target_user=self.md)
        event.note = "changed"
        with self.assertRaises(ValueError):
            event.save()
        with self.assertRaises(ValueError):
            event.delete()
        self.assertEqual(AccountEvent.objects.get(pk=event.pk).note, "")

    def test_event_keeps_the_email_as_it_was_and_survives_the_account(self):
        event = acc.log_account_event(self.co.pk, AccountEvent.Action.CREATED, target_user=self.staff)
        self.staff.delete()
        event.refresh_from_db()
        self.assertEqual((event.target_user, event.target_email), (None, "sam@acme.test"))

    def test_secret_looking_change_keys_are_refused(self):
        for key in ("token", "new_password", "reset_link", "secret"):
            with self.assertRaises(ValueError, msg=key):
                acc.log_account_event(self.co.pk, AccountEvent.Action.PROFILE_UPDATED, changes={key: ["a", "b"]})

    def test_admin_shows_history_read_only_and_company_scoped(self):
        acc.log_account_event(self.co.pk, AccountEvent.Action.CREATED, target_user=self.md)
        acc.log_account_event(self.other.pk, AccountEvent.Action.CREATED, target_user=self.eve)
        ma = admin.site._registry[AccountEvent]
        admin_user = User.objects.create_user("adm", password="x", company=self.co, is_staff=True)
        root = User.objects.create_superuser("root", password="x")
        req = RequestFactory().get("/admin/")
        req.user = admin_user
        self.assertEqual([e.target_email for e in ma.get_queryset(req)], ["ada@acme.test"])
        req.user = root
        self.assertFalse(ma.has_add_permission(req))
        self.assertFalse(ma.has_change_permission(req))
        self.assertFalse(ma.has_delete_permission(req))


class SetupLinkLifecycleTests(Base):
    def test_new_account_has_an_unusable_password_and_cannot_log_in(self):
        self.assertFalse(self.fresh(self.md).has_usable_password())
        self.assertIsNone(authenticate(username="ada@acme.test", password=STRONG))

    def test_valid_setup_link_sets_the_password_and_the_user_can_log_in(self):
        issued = acc.issue_link(self.md, acc.SETUP)
        user = acc.complete_password_link(issued.token, acc.SETUP, STRONG)
        self.assertTrue(self.fresh(self.md).has_usable_password())
        self.assertEqual(user.pk, self.md.pk)
        self.assertIsNotNone(authenticate(username="ada@acme.test", password=STRONG))

    def test_a_link_works_once(self):
        issued = acc.issue_link(self.md, acc.SETUP)
        acc.complete_password_link(issued.token, acc.SETUP, STRONG)
        self.assertInvalid(issued.token)

    def test_setup_link_lasts_three_days_not_more(self):
        issued = acc.issue_link(self.md, acc.SETUP)
        self.assertAlmostEqual((issued.expires_at - acc.timezone.now()).total_seconds(), 3 * DAY, delta=5)
        with later(3 * DAY + 120):
            self.assertInvalid(issued.token)
        with later(3 * DAY - 120):
            acc.complete_password_link(issued.token, acc.SETUP, STRONG)

    def test_reset_link_lasts_one_day_not_more(self):
        issued = acc.issue_link(self.staff, acc.RESET)
        self.assertAlmostEqual((issued.expires_at - acc.timezone.now()).total_seconds(), DAY, delta=5)
        with later(DAY + 120):
            self.assertInvalid(issued.token, acc.RESET)
        with later(DAY - 120):
            acc.complete_password_link(issued.token, acc.RESET, STRONG)

    def test_reset_link_is_used_once(self):
        issued = acc.issue_link(self.staff, acc.RESET)
        acc.complete_password_link(issued.token, acc.RESET, STRONG)
        self.assertInvalid(issued.token, acc.RESET)

    def test_a_setup_token_cannot_be_used_as_a_reset_token_and_the_reverse(self):
        setup = acc.issue_link(self.md, acc.SETUP)
        self.assertInvalid(setup.token, acc.RESET)
        reset = acc.issue_link(self.md, acc.RESET)
        self.assertInvalid(reset.token, acc.SETUP)
        acc.complete_password_link(reset.token, acc.RESET, STRONG)      # the newest one is still fine

    def test_newer_link_invalidates_older_ones(self):
        first = acc.issue_link(self.md, acc.SETUP)
        second = acc.issue_link(self.md, acc.SETUP)
        self.assertInvalid(first.token)
        acc.complete_password_link(second.token, acc.SETUP, STRONG)

    def test_a_reset_link_also_replaces_an_outstanding_setup_link(self):
        setup = acc.issue_link(self.md, acc.SETUP)
        acc.issue_link(self.md, acc.RESET)
        self.assertInvalid(setup.token)

    def test_links_for_other_accounts_are_not_touched(self):
        mine = acc.issue_link(self.md, acc.SETUP)
        acc.issue_link(self.staff, acc.SETUP)
        acc.complete_password_link(mine.token, acc.SETUP, STRONG)

    def test_deactivation_kills_links_and_reactivation_does_not_revive_them(self):
        issued = acc.issue_link(self.staff, acc.SETUP)
        user = self.fresh(self.staff)
        user.is_active = False
        user.save()
        self.assertInvalid(issued.token)
        acc.invalidate_links(user)                   # what the staff API's deactivate step will call
        user.is_active = True
        user.save()
        self.assertInvalid(issued.token)

    def test_changing_the_password_any_other_way_kills_outstanding_links(self):
        issued = acc.issue_link(self.staff, acc.RESET)
        user = self.fresh(self.staff)
        user.set_password("something-else-123")
        user.save()
        self.assertInvalid(issued.token, acc.RESET)

    def test_weak_password_is_rejected_and_the_link_stays_usable(self):
        issued = acc.issue_link(self.md, acc.SETUP)
        for weak in ("short", "12345678", "password"):
            with self.assertRaises(ValidationError, msg=weak):
                acc.complete_password_link(issued.token, acc.SETUP, weak)
        self.assertFalse(self.fresh(self.md).has_usable_password())
        acc.complete_password_link(issued.token, acc.SETUP, STRONG)

    def test_password_too_similar_to_the_account_is_rejected(self):
        issued = acc.issue_link(self.md, acc.SETUP)
        with self.assertRaises(ValidationError):
            acc.complete_password_link(issued.token, acc.SETUP, "ada@acme.test")

    def test_setting_a_password_does_not_log_anyone_in(self):
        issued = acc.issue_link(self.md, acc.SETUP)
        acc.complete_password_link(issued.token, acc.SETUP, STRONG)
        self.assertEqual(self.client.get("/admin/").status_code, 302)    # still anonymous


class BadTokenTests(Base):
    def test_malformed_and_garbage_tokens_fail_the_same_way(self):
        issued = acc.issue_link(self.md, acc.SETUP)
        for bad in (None, "", " ", "abc", "1.setup.1.x:aaa:bbb", issued.token[:-3], issued.token + "x",
                    "A" * 5000, 12345, issued.token.upper()):
            with self.assertRaises(acc.InvalidLink, msg=repr(bad)) as ctx:
                acc.complete_password_link(bad, acc.SETUP, STRONG)
            self.assertEqual(str(ctx.exception), "")
        self.assertFalse(self.fresh(self.md).has_usable_password())

    def test_unknown_purpose_is_refused(self):
        issued = acc.issue_link(self.md, acc.SETUP)
        with self.assertRaises(acc.InvalidLink):
            acc.complete_password_link(issued.token, "admin", STRONG)
        with self.assertRaises(ValueError):
            acc.issue_link(self.md, "admin")

    def test_a_token_cannot_be_pointed_at_another_account(self):
        mine = acc.issue_link(self.md, acc.SETUP)
        theirs = acc.issue_link(self.eve, acc.SETUP)
        value, stamp, sig = mine.token.rsplit(":", 2)
        parts = value.split(".")
        parts[0] = str(self.eve.pk)
        forged = ":".join([".".join(parts), stamp, sig])
        self.assertInvalid(forged)
        # mixing the signature of one with the contents of the other fails as well
        self.assertInvalid(theirs.token.split(":")[0] + ":" + ":".join(mine.token.split(":")[1:]))
        self.assertTrue(self.fresh(self.eve).has_usable_password())      # nobody's password was touched

    def test_a_token_minted_before_the_account_became_protected_stops_working(self):
        issued = acc.issue_link(self.staff, acc.SETUP)
        user = self.fresh(self.staff)
        user.is_staff = True
        user.save()
        self.assertInvalid(issued.token)

    def test_token_for_a_deleted_account_fails(self):
        issued = acc.issue_link(self.staff, acc.SETUP)
        self.staff.delete()
        self.assertInvalid(issued.token)


class IssuingRulesTests(Base):
    def test_protected_inactive_and_companyless_accounts_get_no_link(self):
        admin_user = User.objects.create_user("kd", password="x", company=self.co, is_staff=True)
        root = User.objects.create_superuser("root", password="x", company=self.co)
        gone = User.objects.create_user("gone", password="x", company=self.co, is_active=False)
        nobody = User.objects.create_user("nobody", password="x")
        for user in (admin_user, root, gone, nobody):
            before = self.fresh(user)
            with self.assertRaises(acc.AccountLinkError, msg=user.username):
                acc.issue_link(user, acc.SETUP)
            after = self.fresh(user)
            self.assertEqual((before.password, before.link_serial), (after.password, after.link_serial))
        self.assertEqual(AccountEvent.objects.count(), 0)

    def test_actor_from_another_company_is_refused(self):
        with self.assertRaises(PermissionDenied):
            acc.issue_link(self.md, acc.SETUP, actor=self.eve)
        self.assertEqual(self.fresh(self.md).link_serial, 0)
        self.assertEqual(AccountEvent.objects.count(), 0)

    def test_same_company_actor_is_recorded(self):
        acc.issue_link(self.staff, acc.RESET, actor=self.md)
        event = AccountEvent.objects.get()
        self.assertEqual((event.action, event.actor, event.target_user, event.target_email, event.company),
                         ("reset_link_issued", self.md, self.staff, "sam@acme.test", self.co))

    def test_issuing_records_the_expiry_but_never_the_secret(self):
        issued = acc.issue_link(self.md, acc.SETUP, note="for a locked-out MD")
        event = AccountEvent.objects.get()
        self.assertEqual(event.action, "setup_link_issued")
        self.assertIn("expires_at", event.changes)
        dump = json.dumps(AccountEvent.objects.values_list("changes", "note", "target_email", "action", named=False)[0], default=str)
        self.assertNotIn(issued.token, dump)
        self.assertNotIn("#token", dump)
        self.assertNotIn(issued.token, repr(issued))
        self.assertNotIn(issued.token, str(issued))

    def test_password_set_event_names_the_account_as_actor(self):
        issued = acc.issue_link(self.md, acc.SETUP)
        acc.complete_password_link(issued.token, acc.SETUP, STRONG)
        event = AccountEvent.objects.get(action="password_set")
        self.assertEqual((event.actor, event.target_user, event.note), (self.md, self.md, "Setup link"))
        for e in AccountEvent.objects.all():
            self.assertNotIn(issued.token, json.dumps([e.changes, e.note, e.target_email]))


@override_settings(SITE_URL="https://app.example.test/")
class DeliveryTests(Base):
    @override_settings(EMAIL_DELIVERY_ENABLED=False)
    def test_email_disabled_shows_the_link_once_and_audits_it(self):
        outcome = acc.issue_and_deliver(self.md, acc.SETUP, actor=None)
        self.assertEqual(outcome.status, "revealed")
        self.assertTrue(outcome.link.startswith("https://app.example.test/set-password#token="))
        self.assertNotIn("?", outcome.link)                      # the secret is in the fragment, not the query
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(AccountEvent.objects.filter(action="link_revealed").count(), 1)
        acc.complete_password_link(token_of(outcome.link), acc.SETUP, STRONG)
        self.assertNotIn(outcome.link, repr(outcome))

    @override_settings(EMAIL_DELIVERY_ENABLED=True)
    def test_email_enabled_success_sends_the_link_and_returns_nothing(self):
        outcome = acc.issue_and_deliver(self.staff, acc.RESET, actor=self.md)
        self.assertEqual((outcome.status, outcome.link), ("emailed", None))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["sam@acme.test"])
        self.assertIn("https://app.example.test/reset-password#token=", mail.outbox[0].body)
        self.assertIn("expires in 1 day", mail.outbox[0].body)
        self.assertEqual(AccountEvent.objects.filter(action__in=["email_failed", "link_revealed"]).count(), 0)
        emailed_token = re.search(r"#token=(\S+)", mail.outbox[0].body).group(1)
        acc.complete_password_link(emailed_token, acc.RESET, STRONG)

    @override_settings(EMAIL_DELIVERY_ENABLED=True)
    def test_email_failure_is_audited_and_the_link_is_not_exposed(self):
        with patch("tracker.account_services.send_mail", side_effect=SMTPException("boom")):
            with self.assertLogs("tracker.account_services", "WARNING") as logs:
                outcome = acc.issue_and_deliver(self.staff, acc.SETUP, actor=self.md)
        self.assertEqual((outcome.status, outcome.link), ("email_failed", None))
        self.assertEqual(AccountEvent.objects.filter(action="email_failed").count(), 1)
        self.assertEqual(AccountEvent.objects.filter(action="link_revealed").count(), 0)
        self.assertNotIn("#token", "".join(logs.output))
        self.assertNotIn("boom", "".join(logs.output))           # only the error type is logged

    @override_settings(EMAIL_DELIVERY_ENABLED=True)
    def test_explicit_fallback_reveals_the_link_and_audits_both_events(self):
        with patch("tracker.account_services.send_mail", side_effect=OSError("down")), \
                self.assertLogs("tracker.account_services", "WARNING"):
            outcome = acc.issue_and_deliver(self.staff, acc.SETUP, actor=self.md, reveal_if_email_fails=True)
        self.assertEqual(outcome.status, "revealed")
        self.assertIn("#token=", outcome.link)
        self.assertEqual([e.action for e in AccountEvent.objects.filter(action__in=["email_failed", "link_revealed"])],
                         ["email_failed", "link_revealed"])
        self.assertEqual(AccountEvent.objects.get(action="link_revealed").actor, self.md)
        for e in AccountEvent.objects.all():
            self.assertNotIn(token_of(outcome.link), json.dumps([e.changes, e.note, e.target_email]))

    @override_settings(EMAIL_DELIVERY_ENABLED=True)
    def test_an_account_without_an_email_address_counts_as_a_failed_delivery(self):
        demo = User.objects.create_user("md_demohomes", password="x", company=self.co, role="md")
        outcome = acc.issue_and_deliver(demo, acc.SETUP)
        self.assertEqual(outcome.status, "email_failed")
        self.assertEqual(len(mail.outbox), 0)
        self.assertIn("no email", AccountEvent.objects.get(action="email_failed").note)


@skipUnless(connection.vendor == "postgresql", "needs PostgreSQL row locks")
class ConcurrentUseTests(TransactionTestCase):
    def test_one_link_cannot_be_used_twice_at_the_same_time(self):
        co = Company.objects.create(name="Race Co")
        user = User.objects.create_user("race@x.test", email="race@x.test", company=co, role="md")
        user.set_unusable_password()
        user.save()
        issued = acc.issue_link(user, acc.SETUP)
        barrier, results = threading.Barrier(2), []

        def worker(password):
            try:
                barrier.wait()
                acc.complete_password_link(issued.token, acc.SETUP, password)
                results.append("ok")
            except acc.InvalidLink:
                results.append("invalid")
            finally:
                connection.close()

        threads = [threading.Thread(target=worker, args=(f"Tr1cky-Horse-Battery-{n}",)) for n in (41, 42)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(results), ["invalid", "ok"])
        self.assertEqual(AccountEvent.objects.filter(action="password_set").count(), 1)
