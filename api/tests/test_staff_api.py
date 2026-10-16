import json
import re
from datetime import date
from smtplib import SMTPException
from unittest.mock import patch

from django.core import mail
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APIClient, APITestCase

from api.permissions import ALL_ROLES, MATRIX
from tracker import account_services as acc
from tracker import staff_services
from tracker import services
from tracker.models import (
    AccountEvent, Buyer, Company, Department, Estate, Obligation, ObligationEvent, ObligationType, Plot, Sale, User,
)

MD5 = ["django.contrib.auth.hashers.MD5PasswordHasher"]
STRONG = "Tr1cky-Horse-Battery-42"


def make_user(company, email, role, dept=None, **extra):
    return User.objects.create_user(email, email=email, company=company, role=role, department=dept,
                                    first_name=email.split("@")[0].title(), last_name="Test", **extra)


@override_settings(PASSWORD_HASHERS=MD5, EMAIL_DELIVERY_ENABLED=False, SITE_URL="https://app.example.test")
class StaffApiBase(APITestCase):
    def setUp(self):
        cache.clear()
        self.co = Company.objects.create(name="Acme")
        self.other = Company.objects.create(name="Other")
        self.legal = Department.objects.create(company=self.co, name="Legal")
        self.odept = Department.objects.create(company=self.other, name="Legal")
        self.md, self.md2 = make_user(self.co, "md@acme.test", "md"), make_user(self.co, "md2@acme.test", "md")
        self.mgr, self.mgr2 = make_user(self.co, "mgr@acme.test", "manager"), make_user(self.co, "mgr2@acme.test", "manager")
        self.staff = make_user(self.co, "staff@acme.test", "staff", self.legal)
        self.sales = make_user(self.co, "sales@acme.test", "sales")
        self.viewer = make_user(self.co, "viewer@acme.test", "viewer")
        self.kenny = make_user(self.co, "kenny@acme.test", "md", is_staff=True, is_superuser=True)
        self.omd = make_user(self.other, "omd@other.test", "md")
        self.ostaff = make_user(self.other, "ostaff@other.test", "staff", self.odept)
        self.n = 0

    # ---- helpers ----
    def as_user(self, user):
        client = APIClient()                       # a separate client per person, so two people never share one
        client.force_authenticate(user)
        return client

    def url(self, name, pk=None):
        return reverse(f"staff-{name}", args=[pk] if pk is not None else None)

    def fresh(self, user):
        return User.objects.get(pk=user.pk)

    def payload(self, **kw):
        self.n += 1
        return {"email": f"new{self.n}@acme.test", "first_name": "Nia", "last_name": "Eze", "role": "viewer", **kw}

    def make_obligation(self, owner, status="not_started", sale_status="active"):
        self.n += 1
        estate, _ = Estate.objects.get_or_create(company=owner.company, name="E")
        plot = Plot.objects.create(company=owner.company, estate=estate, plot_number=f"P{self.n}")
        buyer = Buyer.objects.create(company=owner.company, full_name=f"B{self.n}")
        sale = Sale.objects.create(company=owner.company, plot=plot, buyer=buyer, price=100,
                                   sale_date=date(2026, 1, 1), status=sale_status)
        kind = ObligationType.objects.create(company=owner.company, name=f"T{self.n}")
        return Obligation.objects.create(company=owner.company, sale=sale, obligation_type=kind, status=status,
                                         owner=owner, started_on=date(2026, 1, 1))


class PermissionTests(StaffApiBase):
    def all_calls(self, target):
        return [
            ("get", self.url("list"), None), ("get", self.url("detail", target.pk), None),
            ("post", self.url("list"), self.payload()), ("patch", self.url("detail", target.pk), {"first_name": "X"}),
            ("post", self.url("deactivate", target.pk), {}), ("post", self.url("reactivate", target.pk), {}),
            ("post", self.url("setup-link", target.pk), {}), ("post", self.url("reset-link", target.pk), {}),
            ("get", self.url("history", target.pk), None), ("get", self.url("open-obligations", target.pk), None),
            ("get", self.url("options"), None),
        ]

    def test_roles_without_manage_users_are_refused_everywhere(self):
        for actor in (self.staff, self.sales, self.viewer):
            before = User.objects.count()
            for method, url, body in self.all_calls(self.viewer):
                r = getattr(self.as_user(actor), method)(url, body, format="json") if body is not None \
                    else getattr(self.as_user(actor), method)(url)
                self.assertEqual(r.status_code, 403, f"{actor.role} {method} {url}")
            self.assertEqual(User.objects.count(), before)
            self.assertEqual(AccountEvent.objects.count(), 0)

    def test_anonymous_and_companyless_users_are_refused(self):
        self.client.force_authenticate(None)
        for method, url, body in self.all_calls(self.viewer):
            r = getattr(self.client, method)(url, body, format="json") if body is not None else getattr(self.client, method)(url)
            self.assertIn(r.status_code, (401, 403), url)
        lonely = User.objects.create_user("lonely", role="md")
        for method, url, body in self.all_calls(self.viewer):
            r = getattr(self.as_user(lonely), method)(url, body, format="json") if body is not None \
                else getattr(self.as_user(lonely), method)(url)
            self.assertEqual(r.status_code, 403, url)

    def test_matrix_row_and_the_role_rules_agree(self):
        self.assertEqual(MATRIX["manage_users"], {"md", "manager"})
        self.assertEqual({r for r in ALL_ROLES if r in MATRIX["manage_users"]}, set(staff_services.ROLE_MANAGES))

    def test_no_delete_and_no_put(self):
        c = self.as_user(self.md)
        self.assertEqual(c.delete(self.url("detail", self.staff.pk)).status_code, 405)
        self.assertEqual(c.put(self.url("detail", self.staff.pk), {}, format="json").status_code, 405)


class ListAndRetrieveTests(StaffApiBase):
    def test_list_is_company_scoped_and_shows_read_only_flags(self):
        rows = {r["email"]: r for r in self.as_user(self.md).get(self.url("list")).data["results"]}
        self.assertNotIn("omd@other.test", rows)
        self.assertNotIn("ostaff@other.test", rows)
        self.assertEqual(set(rows), {"md@acme.test", "md2@acme.test", "mgr@acme.test", "mgr2@acme.test",
                                     "staff@acme.test", "sales@acme.test", "viewer@acme.test", "kenny@acme.test"})
        self.assertTrue(rows["kenny@acme.test"]["is_protected"])
        self.assertFalse(rows["kenny@acme.test"]["can_manage"])
        self.assertTrue(rows["md2@acme.test"]["can_manage"])
        self.assertTrue(rows["md@acme.test"]["is_self"])
        for secret in ("password", "token", "link", "link_serial", "is_staff", "is_superuser"):
            self.assertFalse(any(secret in key for key in rows["staff@acme.test"]), secret)

    def test_manager_sees_md_and_manager_rows_read_only(self):
        rows = {r["email"]: r for r in self.as_user(self.mgr).get(self.url("list")).data["results"]}
        self.assertIn("md@acme.test", rows)
        for email in ("md@acme.test", "md2@acme.test", "mgr@acme.test", "mgr2@acme.test", "kenny@acme.test"):
            self.assertFalse(rows[email]["can_manage"], email)
        for email in ("staff@acme.test", "sales@acme.test", "viewer@acme.test"):
            self.assertTrue(rows[email]["can_manage"], email)
        self.assertEqual(self.as_user(self.mgr).get(self.url("detail", self.md.pk)).status_code, 200)

    def test_search_and_filters(self):
        c = self.as_user(self.md)
        emails = lambda **q: {r["email"] for r in c.get(self.url("list"), q).data["results"]}  # noqa: E731
        self.assertEqual(emails(q="STAFF@"), {"staff@acme.test"})
        self.assertEqual(emails(role="manager"), {"mgr@acme.test", "mgr2@acme.test"})
        self.assertEqual(emails(department=self.legal.pk), {"staff@acme.test"})
        self.assertEqual(emails(department=self.odept.pk), set())               # another company's department finds nothing
        User.objects.filter(pk=self.viewer.pk).update(is_active=False)
        self.assertEqual(emails(is_active="false"), {"viewer@acme.test"})
        self.assertNotIn("viewer@acme.test", emails(is_active="true"))

    def test_open_obligation_count_in_list_detail_and_its_own_endpoint(self):
        self.make_obligation(self.staff, "not_started")
        self.make_obligation(self.staff, "in_progress")
        self.make_obligation(self.staff, "completed")
        self.make_obligation(self.staff, "not_started", sale_status="cancelled")
        c = self.as_user(self.md)
        listed = {r["email"]: r for r in c.get(self.url("list")).data["results"]}
        self.assertEqual(listed["staff@acme.test"]["open_obligations"], 2)
        self.assertEqual(listed["sales@acme.test"]["open_obligations"], 0)
        self.assertEqual(c.get(self.url("detail", self.staff.pk)).data["open_obligations"], 2)
        r = c.get(self.url("open-obligations", self.staff.pk))
        self.assertEqual((r.data["count"], r.data["owner"]), (2, self.staff.pk))
        self.assertEqual(staff_services.open_obligation_count(self.staff),
                         services.open_obligations(self.co).filter(owner=self.staff).count())

    def test_role_options_follow_the_actor(self):
        roles = lambda u: [x["value"] for x in self.as_user(u).get(self.url("options")).data["roles"]]  # noqa: E731
        self.assertEqual(roles(self.md), ["md", "manager", "staff", "sales", "viewer"])
        self.assertEqual(roles(self.mgr), ["staff", "sales", "viewer"])

    def test_users_picker_contract_is_unchanged(self):
        r = self.as_user(self.md).get(reverse("user-list"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(set(r.data["results"][0]), {"id", "username", "full_name", "role", "department"})
        usernames = {u["username"] for u in r.data["results"]}
        self.assertNotIn("omd@other.test", usernames)
        ss = self.as_user(self.md)
        ss.post(self.url("deactivate", self.staff.pk), {}, format="json")
        usernames = {u["username"] for u in self.as_user(self.md).get(reverse("user-list")).data["results"]}
        self.assertNotIn("staff@acme.test", usernames)                          # inactive people still leave the picker
        self.assertEqual(self.as_user(self.viewer).get(reverse("user-list")).status_code, 200)


class CreateTests(StaffApiBase):
    def test_md_creates_each_role(self):
        c = self.as_user(self.md)
        for role in ("md", "manager", "staff", "sales", "viewer"):
            body = self.payload(role=role, **({"department": self.legal.pk} if role == "staff" else {}))
            r = c.post(self.url("list"), body, format="json")
            self.assertEqual(r.status_code, 201, (role, r.data))
            self.assertEqual((r.data["role"], r.data["is_active"], r.data["email"], r.data["username"]),
                             (role, True, body["email"], body["email"]))
            user = User.objects.get(pk=r.data["id"])
            self.assertEqual(user.company_id, self.co.pk)
            self.assertFalse(user.has_usable_password())
            for key in r.data:
                self.assertNotIn("password", key)

    def test_manager_creates_staff_sales_viewer_but_not_md_or_manager(self):
        c = self.as_user(self.mgr)
        for role in ("staff", "sales", "viewer"):
            body = self.payload(role=role, **({"department": self.legal.pk} if role == "staff" else {}))
            self.assertEqual(c.post(self.url("list"), body, format="json").status_code, 201, role)
        before = User.objects.count()
        for role in ("md", "manager"):
            self.assertEqual(c.post(self.url("list"), self.payload(role=role), format="json").status_code, 403, role)
        self.assertEqual(User.objects.count(), before)

    def test_a_client_supplied_company_is_ignored_and_other_companies_departments_are_refused(self):
        c = self.as_user(self.md)
        r = c.post(self.url("list"), self.payload(company=self.other.pk, company_id=self.other.pk), format="json")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(User.objects.get(pk=r.data["id"]).company_id, self.co.pk)
        r = c.post(self.url("list"), self.payload(role="staff", department=self.odept.pk), format="json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Company.objects.get(pk=self.other.pk).users.count(), 2)

    def test_validation(self):
        c = self.as_user(self.md)
        post = lambda **kw: c.post(self.url("list"), self.payload(**kw), format="json")  # noqa: E731
        self.assertIn("department", post(role="staff").data)
        for role in ("sales", "viewer", "manager"):
            self.assertEqual(post(role=role).status_code, 201, role)
        for bad in (dict(first_name=""), dict(last_name=""), dict(first_name=" "), dict(email="nope"), dict(role="boss"),
                    dict(email="")):
            self.assertEqual(post(**bad).status_code, 400, str(bad))
        r = c.post(self.url("list"), {"email": "x@acme.test"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(set(r.data), {"first_name", "last_name", "role"})

    def test_duplicate_email_is_refused_system_wide_with_the_same_message(self):
        c = self.as_user(self.md)
        messages = set()
        for email in ("staff@acme.test", "STAFF@ACME.TEST", "ostaff@other.test", "kenny@acme.test"):
            r = c.post(self.url("list"), self.payload(email=email), format="json")
            self.assertEqual(r.status_code, 400, email)
            messages.add(r.data["email"][0])
        self.assertEqual(len(messages), 1)

    def test_creating_is_recorded_and_returns_no_secret(self):
        r = self.as_user(self.mgr).post(self.url("list"), self.payload(role="sales", email="Sally@Acme.Test"), format="json")
        event = AccountEvent.objects.get(action="created")
        self.assertEqual((event.actor, event.target_email, event.company), (self.mgr, "sally@acme.test", self.co))
        text = json.dumps(r.data) + json.dumps(event.changes)
        for word in ("token", "#token", "password", "http"):
            self.assertNotIn(word, text)


class UpdateTests(StaffApiBase):
    def test_edit_profile_and_role_with_history(self):
        c = self.as_user(self.mgr)
        r = c.patch(self.url("detail", self.staff.pk), {"first_name": "Samuel", "role": "sales"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual((r.data["first_name"], r.data["role"]), ("Samuel", "sales"))
        actions = list(AccountEvent.objects.values_list("action", flat=True))
        self.assertEqual(sorted(actions), ["profile_updated", "role_changed"])
        self.assertEqual(AccountEvent.objects.get(action="role_changed").changes, {"role": ["staff", "sales"]})

    def test_email_cannot_be_edited(self):
        c = self.as_user(self.md)
        r = c.patch(self.url("detail", self.staff.pk), {"email": "other@acme.test", "first_name": "X"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("email", r.data)
        staff = self.fresh(self.staff)
        self.assertEqual((staff.email, staff.username, staff.first_name), ("staff@acme.test", "staff@acme.test", "Staff"))

    def test_nothing_to_update_and_unknown_fields(self):
        c = self.as_user(self.md)
        self.assertEqual(c.patch(self.url("detail", self.staff.pk), {}, format="json").status_code, 400)
        self.assertEqual(c.patch(self.url("detail", self.staff.pk), {"first_name": "Staff"}, format="json").status_code, 400)
        r = c.patch(self.url("detail", self.staff.pk), {"is_staff": True, "is_superuser": True, "is_active": False,
                                                        "company": self.other.pk, "first_name": "Ok"}, format="json")
        self.assertEqual(r.status_code, 200)
        staff = self.fresh(self.staff)
        self.assertEqual((staff.is_staff, staff.is_superuser, staff.is_active, staff.company_id), (False, False, True, self.co.pk))

    def test_manager_limits(self):
        c = self.as_user(self.mgr)
        for target in (self.md, self.mgr2, self.kenny):
            self.assertEqual(c.patch(self.url("detail", target.pk), {"first_name": "X"}, format="json").status_code, 403)
        for role in ("manager", "md"):
            self.assertEqual(c.patch(self.url("detail", self.staff.pk), {"role": role}, format="json").status_code, 403)
        self.assertEqual(c.patch(self.url("detail", self.mgr.pk), {"role": "viewer"}, format="json").status_code, 403)
        self.assertEqual(self.fresh(self.mgr).role, "manager")

    def test_nobody_changes_their_own_role_but_md_can_manage_another_md(self):
        c = self.as_user(self.md)
        self.assertEqual(c.patch(self.url("detail", self.md.pk), {"role": "viewer"}, format="json").status_code, 403)
        self.assertEqual(self.fresh(self.md).role, "md")
        self.assertEqual(c.patch(self.url("detail", self.md2.pk), {"role": "manager"}, format="json").status_code, 200)
        self.assertEqual(c.patch(self.url("detail", self.md.pk), {"first_name": "Mine"}, format="json").status_code, 200)

    def test_staff_role_needs_a_department(self):
        c = self.as_user(self.md)
        self.assertEqual(c.patch(self.url("detail", self.sales.pk), {"role": "staff"}, format="json").status_code, 400)
        r = c.patch(self.url("detail", self.sales.pk), {"role": "staff", "department": self.legal.pk}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(c.patch(self.url("detail", self.staff.pk), {"department": None}, format="json").status_code, 400)
        self.assertEqual(c.patch(self.url("detail", self.staff.pk), {"department": self.odept.pk}, format="json").status_code, 400)


class IsolationAndProtectionTests(StaffApiBase):
    def test_other_company_accounts_are_404_for_every_endpoint_and_nothing_changes(self):
        before = {u.pk: (u.role, u.is_active, u.first_name, u.link_serial) for u in (self.ostaff, self.omd)}
        for actor in (self.md, self.mgr):
            c = self.as_user(actor)
            for target in (self.ostaff, self.omd):
                for method, name, body in (
                    ("get", "detail", None), ("patch", "detail", {"first_name": "X"}), ("post", "deactivate", {}),
                    ("post", "reactivate", {}), ("post", "setup-link", {}), ("post", "reset-link", {}),
                    ("get", "history", None), ("get", "open-obligations", None),
                ):
                    url = self.url(name, target.pk)
                    r = getattr(c, method)(url, body, format="json") if body is not None else getattr(c, method)(url)
                    self.assertEqual(r.status_code, 404, f"{actor.role} {method} {name}")
        self.assertEqual(before, {u.pk: (u.role, u.is_active, u.first_name, u.link_serial) for u in (self.ostaff, self.omd)})
        self.assertEqual(self.as_user(self.md).get(self.url("detail", 999999)).status_code, 404)   # same as a missing id
        self.assertEqual(AccountEvent.objects.count(), 0)

    def test_django_admin_accounts_are_read_only_through_the_api(self):
        c = self.as_user(self.md)
        before = (self.kenny.password, self.kenny.is_active, self.kenny.role, self.kenny.link_serial, self.kenny.first_name)
        for method, name, body in (
            ("patch", "detail", {"first_name": "X"}), ("patch", "detail", {"role": "viewer"}), ("post", "deactivate", {}),
            ("post", "reactivate", {}), ("post", "setup-link", {}), ("post", "reset-link", {}),
        ):
            r = getattr(c, method)(self.url(name, self.kenny.pk), body, format="json")
            self.assertEqual(r.status_code, 403, f"{method} {name}")
        k = self.fresh(self.kenny)
        self.assertEqual((k.password, k.is_active, k.role, k.link_serial, k.first_name), before)
        self.assertEqual(c.get(self.url("detail", self.kenny.pk)).status_code, 200)       # visible, read-only
        self.assertEqual(AccountEvent.objects.count(), 0)
        for flag in ("is_staff", "is_superuser"):                                          # each flag alone protects
            other = make_user(self.co, f"{flag}@acme.test", "viewer", **{flag: True})
            self.assertEqual(c.post(self.url("deactivate", other.pk), {}, format="json").status_code, 403, flag)


class DeactivationTests(StaffApiBase):
    def test_deactivate_reports_open_obligations_and_never_reassigns_them(self):
        self.make_obligation(self.staff, "not_started")
        self.make_obligation(self.staff, "awaiting_external")
        self.make_obligation(self.staff, "completed")
        events = ObligationEvent.objects.count()
        r = self.as_user(self.mgr).post(self.url("deactivate", self.staff.pk), {"reason": "Resigned"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual((r.data["is_active"], r.data["open_obligations_remaining"]), (False, 2))
        self.assertEqual(Obligation.objects.filter(owner=self.staff).count(), 3)
        self.assertEqual(ObligationEvent.objects.count(), events)
        self.assertIn("2 open obligation(s)", AccountEvent.objects.get(action="deactivated").note)
        self.assertEqual(self.as_user(self.mgr).get(self.url("detail", self.staff.pk)).data["open_obligations"], 2)

    def test_state_and_self_and_role_rules(self):
        c = self.as_user(self.md)
        self.assertEqual(c.post(self.url("reactivate", self.staff.pk), {}, format="json").status_code, 409)
        self.assertEqual(c.post(self.url("deactivate", self.staff.pk), {}, format="json").status_code, 200)
        self.assertEqual(c.post(self.url("deactivate", self.staff.pk), {}, format="json").status_code, 409)
        self.assertEqual(c.post(self.url("reactivate", self.staff.pk), {}, format="json").status_code, 200)
        self.assertEqual(c.post(self.url("deactivate", self.md.pk), {}, format="json").status_code, 403)
        self.assertTrue(self.fresh(self.md).is_active)
        m = self.as_user(self.mgr)
        for target in (self.md, self.mgr2, self.mgr):
            self.assertEqual(m.post(self.url("deactivate", target.pk), {}, format="json").status_code, 403)
        c.post(self.url("deactivate", self.md2.pk), {}, format="json")
        self.assertFalse(self.fresh(self.md2).is_active)
        self.assertEqual(m.post(self.url("reactivate", self.md2.pk), {}, format="json").status_code, 403)
        self.assertEqual(c.post(self.url("reactivate", self.md2.pk), {}, format="json").status_code, 200)

    def test_inactive_accounts_leave_work_queues_and_pickers_but_keep_their_history(self):
        self.as_user(self.md).post(self.url("deactivate", self.staff.pk), {}, format="json")
        picker = {u["username"] for u in self.as_user(self.md).get(reverse("user-list")).data["results"]}
        self.assertNotIn("staff@acme.test", picker)
        self.assertEqual(self.as_user(self.md).get(self.url("history", self.staff.pk)).status_code, 200)

    def test_a_deactivated_user_with_a_live_session_is_signed_out_and_cannot_sign_in(self):
        staff = self.fresh(self.staff)
        staff.set_password(STRONG)
        staff.save()
        session = APIClient(enforce_csrf_checks=True)
        session.get("/api/auth/csrf/")
        token = session.cookies["csrftoken"].value
        r = session.post("/api/auth/login/", {"username": "staff@acme.test", "password": STRONG}, format="json",
                         HTTP_X_CSRFTOKEN=token)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(session.get("/api/auth/me/").status_code, 200)
        self.as_user(self.md).post(self.url("deactivate", self.staff.pk), {}, format="json")
        self.assertIn(session.get("/api/auth/me/").status_code, (401, 403))              # the session no longer works
        token = session.cookies["csrftoken"].value
        r = session.post("/api/auth/login/", {"username": "staff@acme.test", "password": STRONG}, format="json",
                         HTTP_X_CSRFTOKEN=token)
        self.assertEqual((r.status_code, r.data), (400, {"detail": "Invalid credentials."}))


class LinkApiTests(StaffApiBase):
    def test_email_disabled_shows_the_link_once_and_it_works(self):
        c = self.as_user(self.md)
        r = c.post(self.url("setup-link", self.staff.pk), {}, format="json")
        self.assertEqual((r.status_code, r.data["status"], r.data["purpose"]), (200, "revealed", "setup"))
        self.assertTrue(r.data["link"].startswith("https://app.example.test/set-password#token="))
        self.assertIn("expires_at", r.data)
        self.assertEqual(r["Cache-Control"], "no-store")
        token = r.data["link"].split("#token=")[1]
        # nothing else ever returns it
        everything = "".join(json.dumps(x.data) for x in (
            c.get(self.url("list")), c.get(self.url("detail", self.staff.pk)), c.get(self.url("history", self.staff.pk)),
            c.get(self.url("open-obligations", self.staff.pk))))
        self.assertNotIn(token, everything)
        self.assertNotIn("#token", everything)
        self.assertEqual(AccountEvent.objects.filter(action="link_revealed", actor=self.md).count(), 1)
        done = APIClient().post(reverse("auth-set-password"), {"token": token, "password": STRONG}, format="json")
        self.assertEqual(done.status_code, 200, done.data)

    def test_a_newer_link_replaces_the_older_one(self):
        c = self.as_user(self.md)
        first = c.post(self.url("reset-link", self.staff.pk), {}, format="json").data["link"].split("#token=")[1]
        second = c.post(self.url("reset-link", self.staff.pk), {}, format="json").data["link"].split("#token=")[1]
        public = APIClient()
        self.assertEqual(public.post(reverse("auth-reset-password"), {"token": first, "password": STRONG}, format="json").status_code, 400)
        self.assertEqual(public.post(reverse("auth-reset-password"), {"token": second, "password": STRONG}, format="json").status_code, 200)

    def test_manager_cannot_issue_links_for_md_or_manager_but_can_for_staff(self):
        c = self.as_user(self.mgr)
        for target in (self.md, self.mgr2, self.kenny):
            for name in ("setup-link", "reset-link"):
                self.assertEqual(c.post(self.url(name, target.pk), {}, format="json").status_code, 403)
        self.assertEqual(c.post(self.url("setup-link", self.staff.pk), {}, format="json").status_code, 200)
        self.assertEqual(self.as_user(self.md).post(self.url("reset-link", self.md2.pk), {}, format="json").status_code, 200)
        self.assertEqual(AccountEvent.objects.filter(action="setup_link_issued", target_user=self.md).count(), 0)

    def test_inactive_account_gets_409(self):
        c = self.as_user(self.md)
        c.post(self.url("deactivate", self.staff.pk), {}, format="json")
        self.assertEqual(c.post(self.url("setup-link", self.staff.pk), {}, format="json").status_code, 409)

    @override_settings(EMAIL_DELIVERY_ENABLED=True)
    def test_email_enabled_success_sends_and_returns_no_link(self):
        r = self.as_user(self.md).post(self.url("setup-link", self.staff.pk), {}, format="json")
        self.assertEqual((r.status_code, r.data["status"]), (200, "emailed"))
        self.assertNotIn("link", r.data)
        self.assertNotIn("token", json.dumps(r.data))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["staff@acme.test"])
        token = re.search(r"#token=(\S+)", mail.outbox[0].body).group(1)
        done = APIClient().post(reverse("auth-set-password"), {"token": token, "password": STRONG}, format="json")
        self.assertEqual(done.status_code, 200)

    @override_settings(EMAIL_DELIVERY_ENABLED=True)
    def test_email_failure_is_audited_and_the_link_is_shown_only_on_explicit_request(self):
        c = self.as_user(self.md)
        with patch("tracker.account_services.send_mail", side_effect=SMTPException("down")), \
                self.assertLogs("tracker.account_services", "WARNING"):
            r = c.post(self.url("setup-link", self.staff.pk), {}, format="json")
            self.assertEqual((r.status_code, r.data["status"]), (200, "email_failed"))
            self.assertNotIn("link", r.data)
            self.assertEqual(AccountEvent.objects.filter(action="link_revealed").count(), 0)
            self.assertEqual(AccountEvent.objects.filter(action="email_failed").count(), 1)
            r = c.post(self.url("setup-link", self.staff.pk), {"reveal_if_email_fails": True}, format="json")
            self.assertEqual(r.data["status"], "revealed")
            self.assertIn("#token=", r.data["link"])
        self.assertEqual(AccountEvent.objects.filter(action="link_revealed", actor=self.md).count(), 1)
        for event in AccountEvent.objects.all():
            self.assertNotIn(r.data["link"].split("#token=")[1], json.dumps([event.changes, event.note]))

    def test_deactivation_kills_an_outstanding_link(self):
        c = self.as_user(self.md)
        token = c.post(self.url("setup-link", self.staff.pk), {}, format="json").data["link"].split("#token=")[1]
        c.post(self.url("deactivate", self.staff.pk), {}, format="json")
        c.post(self.url("reactivate", self.staff.pk), {}, format="json")
        r = APIClient().post(reverse("auth-set-password"), {"token": token, "password": STRONG}, format="json")
        self.assertEqual(r.status_code, 400)


class HistoryApiTests(StaffApiBase):
    def test_history_content_and_scope(self):
        c = self.as_user(self.mgr)
        c.patch(self.url("detail", self.staff.pk), {"first_name": "Samuel"}, format="json")
        c.post(self.url("deactivate", self.staff.pk), {"reason": "Left"}, format="json")
        r = c.get(self.url("history", self.staff.pk))
        self.assertEqual(r.status_code, 200)
        rows = r.data["results"]
        self.assertEqual([x["action"] for x in rows], ["profile_updated", "deactivated"])
        self.assertEqual(rows[0]["changes"], {"first_name": ["Staff", "Samuel"]})
        self.assertEqual((rows[0]["actor"], rows[0]["actor_name"], rows[0]["target_email"]), (self.mgr.pk, "Mgr Test", "staff@acme.test"))
        self.assertEqual(rows[1]["action_label"], "Account deactivated")

    def test_manager_cannot_read_md_or_manager_history_but_md_can(self):
        self.as_user(self.md).patch(self.url("detail", self.mgr2.pk), {"first_name": "Changed"}, format="json")
        for target in (self.md, self.mgr2, self.kenny):
            self.assertEqual(self.as_user(self.mgr).get(self.url("history", target.pk)).status_code, 403)
        r = self.as_user(self.md).get(self.url("history", self.mgr2.pk))
        self.assertEqual((r.status_code, len(r.data["results"])), (200, 1))
        self.assertEqual(self.as_user(self.md).get(self.url("history", self.kenny.pk)).status_code, 200)
