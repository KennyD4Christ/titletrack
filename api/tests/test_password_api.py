import json
import time
from unittest.mock import patch

from django.conf import settings
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APIClient, APITestCase
from rest_framework.throttling import ScopedRateThrottle

from tracker import account_services as acc
from tracker.models import AccountEvent, Company, User

MD5 = ["django.contrib.auth.hashers.MD5PasswordHasher"]
STRONG = "Tr1cky-Horse-Battery-42"
NEWER = "Another-Str0ng-Passphrase-77"
DAY = 24 * 3600
GENERIC = {"detail": "This link is invalid or has expired."}


def later(seconds):
    return patch("django.core.signing.time.time", return_value=time.time() + seconds)


@override_settings(PASSWORD_HASHERS=MD5)
class PasswordApiBase(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.co = Company.objects.create(name="Acme")
        self.md = User.objects.create_user("md@acme.test", email="md@acme.test", company=self.co, role="md",
                                           first_name="Ada", last_name="Obi")
        self.md.set_unusable_password()
        self.md.save()
        self.staff = User.objects.create_user("staff@acme.test", email="staff@acme.test", password="Old-Passw0rd-xx",
                                              company=self.co, role="staff", first_name="Sam", last_name="Eze")
        self.public = APIClient(enforce_csrf_checks=True)
        self.public.get("/api/auth/csrf/")

    def post(self, name, body, client=None):
        client = client or self.public
        return client.post(reverse(name), body, format="json", HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value)

    def signed_in(self, user, password):
        client = APIClient(enforce_csrf_checks=True)
        client.get("/api/auth/csrf/")
        r = client.post("/api/auth/login/", {"username": user.username, "password": password}, format="json",
                        HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value)
        assert r.status_code == 200, r.data
        return client


class SetPasswordTests(PasswordApiBase):
    def test_valid_setup_link_sets_the_password_once_and_does_not_sign_anyone_in(self):
        token = acc.issue_link(self.md, acc.SETUP).token
        r = self.post("auth-set-password", {"token": token, "password": STRONG})
        self.assertEqual(r.status_code, 200, r.data)
        self.assertNotIn("sessionid", r.cookies)
        self.assertIn(self.public.get("/api/auth/me/").status_code, (401, 403))
        self.assertTrue(User.objects.get(pk=self.md.pk).check_password(STRONG))
        again = self.post("auth-set-password", {"token": token, "password": NEWER})
        self.assertEqual((again.status_code, again.data), (400, GENERIC))
        self.assertTrue(User.objects.get(pk=self.md.pk).check_password(STRONG))
        self.signed_in(self.md, STRONG)                                      # the new password really works

    def test_valid_reset_link_and_single_use(self):
        token = acc.issue_link(self.staff, acc.RESET).token
        self.assertEqual(self.post("auth-reset-password", {"token": token, "password": STRONG}).status_code, 200)
        self.assertEqual(self.post("auth-reset-password", {"token": token, "password": NEWER}).data, GENERIC)
        self.signed_in(self.staff, STRONG)
        with self.assertRaises(AssertionError):
            self.signed_in(self.staff, "Old-Passw0rd-xx")

    def test_expiry_three_days_for_setup_one_day_for_reset(self):
        setup, reset = acc.issue_link(self.md, acc.SETUP).token, acc.issue_link(self.staff, acc.RESET).token
        with later(DAY + 120):
            self.assertEqual(self.post("auth-reset-password", {"token": reset, "password": STRONG}).data, GENERIC)
            self.assertEqual(self.post("auth-set-password", {"token": setup, "password": STRONG}).status_code, 200)
        reset = acc.issue_link(self.staff, acc.RESET).token
        with later(DAY - 120):
            self.assertEqual(self.post("auth-reset-password", {"token": reset, "password": STRONG}).status_code, 200)
        setup = acc.issue_link(self.md, acc.SETUP).token
        with later(3 * DAY + 120):
            self.assertEqual(self.post("auth-set-password", {"token": setup, "password": STRONG}).data, GENERIC)

    def test_every_bad_token_gets_the_same_answer(self):
        good = acc.issue_link(self.md, acc.SETUP).token
        other = acc.issue_link(self.staff, acc.SETUP).token
        value, stamp, sig = good.rsplit(":", 2)
        forged = ":".join([value.replace(f"{self.md.pk}.", f"{self.staff.pk}.", 1), stamp, sig])
        for bad in ("", "x", "garbage:garbage:garbage", good[:-2], good + "a", "A" * 5000, forged, other + "x"):
            r = self.post("auth-set-password", {"token": bad or "x", "password": STRONG})
            self.assertEqual((r.status_code, r.data), (400, GENERIC), bad[:20])
        wrong_purpose = self.post("auth-reset-password", {"token": good, "password": STRONG})
        self.assertEqual((wrong_purpose.status_code, wrong_purpose.data), (400, GENERIC))
        self.assertFalse(User.objects.get(pk=self.md.pk).has_usable_password())
        self.assertFalse(User.objects.get(pk=self.staff.pk).check_password(STRONG))

    def test_deactivated_account_and_replaced_link(self):
        old = acc.issue_link(self.md, acc.SETUP).token
        new = acc.issue_link(self.md, acc.SETUP).token
        self.assertEqual(self.post("auth-set-password", {"token": old, "password": STRONG}).data, GENERIC)
        User.objects.filter(pk=self.md.pk).update(is_active=False)
        self.assertEqual(self.post("auth-set-password", {"token": new, "password": STRONG}).data, GENERIC)

    def test_weak_password_is_explained_and_the_link_stays_usable(self):
        token = acc.issue_link(self.md, acc.SETUP).token
        for weak in ("short", "12345678", "password", "md@acme.test"):
            r = self.post("auth-set-password", {"token": token, "password": weak})
            self.assertEqual(r.status_code, 400, weak)
            self.assertIn("password", r.data)
        self.assertEqual(self.post("auth-set-password", {"token": token, "password": STRONG}).status_code, 200)

    def test_missing_fields_and_csrf(self):
        self.assertEqual(self.post("auth-set-password", {"token": "x"}).status_code, 400)
        self.assertEqual(self.post("auth-set-password", {"password": STRONG}).status_code, 400)
        bare = APIClient(enforce_csrf_checks=True)
        r = bare.post(reverse("auth-set-password"), {"token": "x", "password": STRONG}, format="json")
        self.assertEqual(r.status_code, 403)

    def test_history_records_the_password_set_without_the_secret(self):
        token = acc.issue_link(self.md, acc.SETUP).token
        self.post("auth-set-password", {"token": token, "password": STRONG})
        event = AccountEvent.objects.get(action="password_set")
        self.assertEqual((event.target_user, event.actor, event.note), (self.md, self.md, "Setup link"))
        for e in AccountEvent.objects.all():
            self.assertNotIn(token, json.dumps([e.changes, e.note, e.target_email]))
            self.assertNotIn(STRONG, json.dumps([e.changes, e.note, e.target_email]))


class ThrottleTests(PasswordApiBase):
    def rates(self, **rates):
        return patch.dict(ScopedRateThrottle.THROTTLE_RATES, rates)

    def test_configured_defaults(self):
        rest = settings.REST_FRAMEWORK
        self.assertEqual(rest["DEFAULT_THROTTLE_RATES"]["account_link"], "20/hour")
        self.assertEqual(rest["DEFAULT_THROTTLE_RATES"]["password_change"], "10/hour")
        self.assertEqual(rest["NUM_PROXIES"], 0)

    def test_setup_and_reset_share_a_per_ip_budget(self):
        with self.rates(account_link="3/hour"):
            codes = [self.post("auth-set-password", {"token": "x", "password": STRONG}).status_code for _ in range(2)]
            codes.append(self.post("auth-reset-password", {"token": "x", "password": STRONG}).status_code)
            self.assertEqual(codes, [400, 400, 400])
            r = self.post("auth-set-password", {"token": "x", "password": STRONG})
            self.assertEqual(r.status_code, 429)
            self.assertEqual(self.post("auth-reset-password", {"token": "x", "password": STRONG}).status_code, 429)

    def test_a_throttled_client_cannot_use_a_valid_link(self):
        token = acc.issue_link(self.md, acc.SETUP).token
        with self.rates(account_link="2/hour"):
            for _ in range(2):
                self.post("auth-set-password", {"token": "x", "password": STRONG})
            self.assertEqual(self.post("auth-set-password", {"token": token, "password": STRONG}).status_code, 429)
        self.assertFalse(User.objects.get(pk=self.md.pk).has_usable_password())

    def test_another_address_has_its_own_budget_and_a_spoofed_header_does_not_help(self):
        with self.rates(account_link="2/hour"):
            def hit(addr, xff=None):
                extra = {"REMOTE_ADDR": addr, "HTTP_X_CSRFTOKEN": self.public.cookies["csrftoken"].value}
                if xff:
                    extra["HTTP_X_FORWARDED_FOR"] = xff
                return self.public.post(reverse("auth-set-password"), {"token": "x", "password": STRONG},
                                        format="json", **extra).status_code
            self.assertEqual([hit("10.0.0.1", "1.1.1.1"), hit("10.0.0.1", "2.2.2.2")], [400, 400])
            self.assertEqual(hit("10.0.0.1", "3.3.3.3"), 429)          # a changing X-Forwarded-For does not reset it
            self.assertEqual(hit("10.0.0.2"), 400)                      # a genuinely different address does

    def test_num_proxies_makes_the_header_count_only_when_configured(self):
        with self.rates(account_link="1/hour"), override_settings(REST_FRAMEWORK={**settings.REST_FRAMEWORK, "NUM_PROXIES": 1}):
            def hit(xff):
                return self.public.post(reverse("auth-set-password"), {"token": "x", "password": STRONG}, format="json",
                                        REMOTE_ADDR="10.0.0.9", HTTP_X_FORWARDED_FOR=xff,
                                        HTTP_X_CSRFTOKEN=self.public.cookies["csrftoken"].value).status_code
            self.assertEqual(hit("6.6.6.6, 7.7.7.7"), 400)
            self.assertEqual(hit("9.9.9.9, 7.7.7.7"), 429)              # same last (proxy-added) hop: same client
            self.assertEqual(hit("6.6.6.6, 8.8.8.8"), 400)              # a different hop: a different client


class ChangePasswordTests(PasswordApiBase):
    OLD = "Old-Passw0rd-xx"

    def change(self, client, current, new):
        return self.post("auth-change-password", {"current_password": current, "new_password": new}, client=client)

    def test_requires_a_signed_in_company_user(self):
        r = self.public.post(reverse("auth-change-password"), {"current_password": "a", "new_password": "b"}, format="json",
                             HTTP_X_CSRFTOKEN=self.public.cookies["csrftoken"].value)
        self.assertIn(r.status_code, (401, 403))

    def test_success_keeps_this_session_ends_other_sessions_and_kills_links(self):
        mine, other = self.signed_in(self.staff, self.OLD), self.signed_in(self.staff, self.OLD)
        link = acc.issue_link(self.staff, acc.RESET).token
        r = self.change(mine, self.OLD, STRONG)
        self.assertEqual((r.status_code, r.data), (200, {"detail": "Your password has been changed."}))
        self.assertEqual(mine.get("/api/auth/me/").status_code, 200)             # still signed in here
        self.assertIn(other.get("/api/auth/me/").status_code, (401, 403))        # signed out elsewhere
        self.signed_in(self.staff, STRONG)
        with self.assertRaises(AssertionError):
            self.signed_in(self.staff, self.OLD)
        self.assertEqual(self.post("auth-reset-password", {"token": link, "password": NEWER}).data, GENERIC)
        event = AccountEvent.objects.get(action="password_changed")
        self.assertEqual((event.actor, event.target_user, event.changes), (self.staff, self.staff, {}))
        self.assertNotIn(STRONG, json.dumps([event.changes, event.note]))

    def test_refusals_change_nothing(self):
        client = self.signed_in(self.staff, self.OLD)
        before = User.objects.get(pk=self.staff.pk).password
        wrong = self.change(client, "Not-The-Password-1", STRONG)
        self.assertEqual((wrong.status_code, wrong.data), (400, {"current_password": ["Incorrect password."]}))
        for weak in ("short", "12345678", "password", "staff@acme.test"):
            self.assertEqual(self.change(client, self.OLD, weak).status_code, 400, weak)
        self.assertEqual(self.change(client, self.OLD, self.OLD).status_code, 400)
        self.assertEqual(User.objects.get(pk=self.staff.pk).password, before)
        self.assertEqual(AccountEvent.objects.filter(action="password_changed").count(), 0)

    def test_ten_attempts_an_hour_per_user_by_default_is_configurable_and_per_user(self):
        mine = self.signed_in(self.staff, self.OLD)
        md = self.md
        md.set_password(self.OLD)
        md.save()
        theirs = self.signed_in(md, self.OLD)
        with patch.dict(ScopedRateThrottle.THROTTLE_RATES, {"password_change": "3/hour"}):
            codes = [self.change(mine, "wrong-guess-1", STRONG).status_code for _ in range(3)]
            self.assertEqual(codes, [400, 400, 400])
            self.assertEqual(self.change(mine, self.OLD, STRONG).status_code, 429)   # even the right password is refused now
            self.assertEqual(self.change(theirs, self.OLD, STRONG).status_code, 200)  # another user is unaffected
        self.assertTrue(User.objects.get(pk=self.staff.pk).check_password(self.OLD))
