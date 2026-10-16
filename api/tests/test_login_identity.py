from unittest.mock import patch

from django.core.cache import cache
from django.test import override_settings
from rest_framework.test import APIClient, APITestCase

from tracker.models import Company, User

MD5 = ["django.contrib.auth.hashers.MD5PasswordHasher"]
PW = "Tr1cky-Horse-Battery-42"
BAD = {"detail": "Invalid credentials."}


@override_settings(PASSWORD_HASHERS=MD5)
class LoginIdentityTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.co = Company.objects.create(name="Acme")
        mk = lambda username, **kw: User.objects.create_user(username, password=PW, company=self.co, **kw)  # noqa: E731
        self.new = mk("ada@acme.test", email="ada@acme.test", role="md", first_name="Ada")
        self.demo = mk("md_demohomes", role="md", first_name="Demo")                     # old account: username only
        self.legacy = mk("Pat.Old", email="Pat@Home.test", role="staff")                # old account whose email differs
        self.gone = mk("gone@acme.test", email="gone@acme.test", is_active=False)
        self.nocompany = User.objects.create_user("nc@acme.test", email="nc@acme.test", password=PW)
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.get("/api/auth/csrf/")

    def login(self, identifier, password=PW):
        return self.client.post("/api/auth/login/", {"username": identifier, "password": password}, format="json",
                                HTTP_X_CSRFTOKEN=self.client.cookies["csrftoken"].value)

    def test_email_login_ignores_letter_case_and_the_response_is_unchanged(self):
        for identifier in ("ada@acme.test", "ADA@ACME.TEST", "Ada@Acme.Test"):
            r = self.login(identifier)
            self.assertEqual(r.status_code, 200, identifier)
            self.assertEqual(set(r.data), {"id", "username", "full_name", "role", "company", "department"})
            self.assertEqual((r.data["username"], r.data["role"]), ("ada@acme.test", "md"))
            self.client.post("/api/auth/logout/", HTTP_X_CSRFTOKEN=self.client.cookies["csrftoken"].value)
            self.client.get("/api/auth/csrf/")

    def test_existing_usernames_keep_working_exactly_as_before(self):
        self.assertEqual(self.login("md_demohomes").status_code, 200)
        self.client.post("/api/auth/logout/", HTTP_X_CSRFTOKEN=self.client.cookies["csrftoken"].value)
        self.client.get("/api/auth/csrf/")
        self.assertEqual(self.login("Pat.Old").status_code, 200)
        self.client.post("/api/auth/logout/", HTTP_X_CSRFTOKEN=self.client.cookies["csrftoken"].value)
        self.client.get("/api/auth/csrf/")
        self.assertEqual(self.login("pat.old").status_code, 400)           # usernames stay case-sensitive, as before

    def test_an_old_account_can_also_sign_in_with_its_email_address(self):
        self.assertEqual(self.login("pat@HOME.test").status_code, 200)

    def test_every_failure_looks_the_same(self):
        cases = [("ada@acme.test", "wrong"), ("nobody@acme.test", PW), ("gone@acme.test", PW), ("nc@acme.test", PW),
                 ("md_demohomes", "wrong"), ("unknown_user", PW), ("a" * 151 + "@x.test", PW), ("a" * 400, PW),
                 ("@", PW)]
        for identifier, password in cases:
            r = self.login(identifier, password)
            self.assertEqual((r.status_code, r.data), (400, BAD), identifier[:30])

    def test_an_email_that_matches_two_accounts_signs_nobody_in(self):
        # The database now refuses two accounts with one email, so this can only come from old data. Simulate it.
        class TwoMatches:
            def values_list(self, *args, **kwargs):
                return self

            def exists(self):
                return False

            def __getitem__(self, item):
                return ["twin1", "twin2"]

        with patch("api.auth_utils.User.objects.filter", return_value=TwoMatches()):
            r = self.login("twin@acme.test")
        self.assertEqual((r.status_code, r.data), (400, BAD))

    def test_a_username_always_beats_an_email_address(self):
        User.objects.create_user("pat@x.test", password="Username-Owner-9x", company=self.co, email="owner@x.test")
        User.objects.create_user("zed", password=PW, company=self.co, email="Pat@X.test")
        self.assertEqual(self.login("pat@x.test", PW).status_code, 400)                  # zed cannot take the name over
        self.assertEqual(self.login("pat@x.test", "Username-Owner-9x").status_code, 200)

    def test_inactive_and_companyless_accounts_still_cannot_sign_in(self):
        self.assertEqual(self.login("gone@acme.test").status_code, 400)
        self.assertEqual(self.login("nc@acme.test").status_code, 400)

    def test_csrf_is_still_required(self):
        bare = APIClient(enforce_csrf_checks=True)
        r = bare.post("/api/auth/login/", {"username": "ada@acme.test", "password": PW}, format="json")
        self.assertEqual(r.status_code, 403)
