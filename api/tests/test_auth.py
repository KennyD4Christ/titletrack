from rest_framework.test import APIClient, APITestCase

from tracker.models import Company, Department, User


class AuthTests(APITestCase):
    def setUp(self):
        self.co = Company.objects.create(name="A Homes")
        self.dept = Department.objects.create(company=self.co, name="Legal")
        self.user = User.objects.create_user(
            "sarah", password="pw-12345-x", first_name="Sarah", company=self.co, role="staff", department=self.dept)
        self.client = APIClient(enforce_csrf_checks=True)

    def csrf(self):
        self.client.get("/api/auth/csrf/")
        return self.client.cookies["csrftoken"].value

    def login(self, password="pw-12345-x", token=None):
        token = token or self.csrf()
        return self.client.post("/api/auth/login/", {"username": "sarah", "password": password},
                                format="json", HTTP_X_CSRFTOKEN=token)

    def test_csrf_endpoint_sets_cookie(self):
        self.assertEqual(self.client.get("/api/auth/csrf/").status_code, 200)
        self.assertIn("csrftoken", self.client.cookies)

    def test_login_without_csrf_token_is_rejected(self):
        r = self.client.post("/api/auth/login/", {"username": "sarah", "password": "pw-12345-x"}, format="json")
        self.assertEqual(r.status_code, 403)

    def test_login_and_me(self):
        r = self.login()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["company"]["name"], "A Homes")
        me = self.client.get("/api/auth/me/")
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.data["role"], "staff")
        self.assertEqual(me.data["department"]["name"], "Legal")
        self.assertEqual(me.data["full_name"], "Sarah")

    def test_wrong_password(self):
        self.assertEqual(self.login(password="nope").status_code, 400)

    def test_inactive_and_companyless_users_cannot_log_in(self):
        User.objects.create_user("gone", password="pw-12345-x", company=self.co, is_active=False)
        User.objects.create_user("nocomp", password="pw-12345-x")
        token = self.csrf()
        for name in ("gone", "nocomp"):
            r = self.client.post("/api/auth/login/", {"username": name, "password": "pw-12345-x"},
                                 format="json", HTTP_X_CSRFTOKEN=token)
            self.assertEqual(r.status_code, 400, name)

    def test_me_requires_login(self):
        self.assertIn(self.client.get("/api/auth/me/").status_code, (401, 403))

    def test_logout_needs_csrf_then_ends_session(self):
        self.login()
        self.assertEqual(self.client.post("/api/auth/logout/").status_code, 403)  # no CSRF header
        token = self.client.cookies["csrftoken"].value
        self.assertEqual(self.client.post("/api/auth/logout/", HTTP_X_CSRFTOKEN=token).status_code, 204)
        self.assertIn(self.client.get("/api/auth/me/").status_code, (401, 403))
