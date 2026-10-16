import json
import threading
from datetime import date
from unittest import skipUnless
from unittest.mock import patch

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, connection, transaction
from django.test import TestCase, TransactionTestCase

from . import account_services as acc
from . import staff_services as ss
from .models import (
    AccountEvent, Buyer, Company, Department, Estate, Obligation, ObligationEvent, ObligationType, Plot, Sale, User,
)


def make_user(company, email, role, dept=None, **extra):
    return User.objects.create_user(email, email=email, company=company, role=role, department=dept,
                                    first_name=email.split("@")[0].title(), last_name="Test", **extra)


class Base(TestCase):
    def setUp(self):
        self.co = Company.objects.create(name="Acme")
        self.other = Company.objects.create(name="Other")
        self.legal = Department.objects.create(company=self.co, name="Legal")
        self.survey = Department.objects.create(company=self.co, name="Survey")
        self.other_dept = Department.objects.create(company=self.other, name="Legal")
        mk = lambda e, r, d=None, c=None, **x: make_user(c or self.co, e, r, d, **x)   # noqa: E731
        self.md, self.md2 = mk("md@acme.test", "md"), mk("md2@acme.test", "md")
        self.mgr, self.mgr2 = mk("mgr@acme.test", "manager"), mk("mgr2@acme.test", "manager")
        self.staff = mk("staff@acme.test", "staff", self.legal)
        self.sales, self.viewer = mk("sales@acme.test", "sales"), mk("viewer@acme.test", "viewer")
        self.omd = mk("omd@other.test", "md", c=self.other)
        self.ostaff = mk("ostaff@other.test", "staff", self.other_dept, c=self.other)
        self.n = 0

    def fresh(self, user):
        return User.objects.get(pk=user.pk)

    def snapshot(self, *users):
        return {u.pk: (u.role, u.is_active, u.first_name, u.last_name, u.department_id, u.link_serial, u.password)
                for u in User.objects.filter(pk__in=[x.pk for x in users])}

    def new(self, actor, **kw):
        defaults = dict(email=f"new{self.n}@acme.test", first_name="Nia", last_name="Eze", role="viewer")
        self.n += 1
        return ss.create_staff(actor, **{**defaults, **kw})

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


class CreateTests(Base):
    def test_md_creates_every_role_including_another_md(self):
        for role in ss.ALL_ROLES:
            user = self.new(self.md, role=role, department=self.legal if role == "staff" else None,
                            email=f"made_{role}@acme.test")
            self.assertEqual((user.role, user.company_id, user.username, user.email, user.is_active),
                             (role, self.co.pk, f"made_{role}@acme.test", f"made_{role}@acme.test", True))
            self.assertFalse(user.has_usable_password())
            self.assertFalse(user.is_staff or user.is_superuser)

    def test_manager_creates_staff_sales_and_viewer_only(self):
        for role in ("staff", "sales", "viewer"):
            self.new(self.mgr, role=role, department=self.legal if role == "staff" else None)
        before = User.objects.count()
        for role in ("md", "manager"):
            with self.assertRaises(PermissionDenied, msg=role):
                self.new(self.mgr, role=role)
        self.assertEqual(User.objects.count(), before)

    def test_other_roles_manage_nobody(self):
        for actor in (self.staff, self.sales, self.viewer):
            with self.assertRaises(PermissionDenied, msg=actor.role):
                self.new(actor)
            with self.assertRaises(PermissionDenied):
                ss.update_staff(actor, self.viewer.pk, first_name="X")
            with self.assertRaises(PermissionDenied):
                ss.deactivate(actor, self.viewer.pk)
            with self.assertRaises(PermissionDenied):
                ss.reactivate(actor, self.viewer.pk)
            with self.assertRaises(PermissionDenied):
                ss.issue_link_for(actor, self.viewer.pk, acc.RESET)
            with self.assertRaises(PermissionDenied):
                ss.account_history(actor, self.viewer)

    def test_the_company_is_always_the_actors_company(self):
        user = self.new(self.omd, role="staff", department=self.other_dept)
        self.assertEqual(user.company_id, self.other.pk)

    def test_department_rules(self):
        with self.assertRaises(ss.StaffValidationError) as ctx:
            self.new(self.md, role="staff")
        self.assertIn("department", ctx.exception.errors)
        for role in ("manager", "sales", "viewer", "md"):                      # optional for every other role
            self.new(self.md, role=role)
            self.new(self.md, role=role, department=self.survey)
        with self.assertRaises(ss.StaffValidationError) as ctx:
            self.new(self.md, role="viewer", department=self.other_dept)        # another company's department
        self.assertIn("department", ctx.exception.errors)

    def test_names_and_email_are_checked_and_normalised(self):
        user = self.new(self.md, email="  Mixed.Case@Acme.Test ", first_name="  Ada   Lovelace ", last_name=" Obi ")
        self.assertEqual((user.username, user.email, user.first_name, user.last_name),
                         ("mixed.case@acme.test", "mixed.case@acme.test", "Ada Lovelace", "Obi"))
        for bad in (dict(first_name=" "), dict(last_name=""), dict(first_name="x" * 151), dict(email="nope"),
                    dict(email="a" * 64 + "@" + ".".join(["b" * 63, "c" * 63, "d" * 10]) + ".com"), dict(role="boss")):
            with self.assertRaises(ss.StaffValidationError, msg=str(bad)):
                self.new(self.md, **bad)

    def test_duplicate_emails_are_refused_everywhere_with_one_neutral_message(self):
        messages = set()
        for email in ("MD@acme.test", "md@ACME.test", "omd@other.test"):
            with self.assertRaises(ss.StaffValidationError, msg=email) as ctx:
                self.new(self.md, email=email)
            messages.add(ctx.exception.errors["email"][0])
        with self.assertRaises(ss.StaffValidationError) as ctx:        # an old username that looks like an email
            User.objects.create_user("Old@Name.test", company=self.co)
            self.new(self.md, email="old@name.test")
        messages.add(ctx.exception.errors["email"][0])
        self.assertEqual(messages, {ss.EMAIL_TAKEN})                  # same text: in this company, another, or a username

    def test_creating_records_who_what_and_the_after_values_but_no_secret(self):
        user = self.new(self.mgr, role="staff", department=self.legal, email="sam2@acme.test")
        event = AccountEvent.objects.get(target_user=user)
        self.assertEqual((event.action, event.actor, event.company, event.target_email),
                         ("created", self.mgr, self.co, "sam2@acme.test"))
        self.assertEqual(event.changes, {"role": [None, "staff"], "first_name": [None, "Nia"],
                                         "last_name": [None, "Eze"], "department": [None, "Legal"]})
        self.assertNotIn("password", json.dumps(event.changes).lower())

    def test_a_failure_while_recording_the_event_leaves_no_account(self):
        before = User.objects.count()
        with patch("tracker.staff_services.acc.log_account_event", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.new(self.md)
        self.assertEqual(User.objects.count(), before)


class UpdateTests(Base):
    def test_profile_edit_records_before_and_after(self):
        ss.update_staff(self.mgr, self.staff.pk, first_name=" Samuel ", department=self.survey)
        staff = self.fresh(self.staff)
        self.assertEqual((staff.first_name, staff.department), ("Samuel", self.survey))
        event = AccountEvent.objects.get(action="profile_updated")
        self.assertEqual(event.changes, {"first_name": ["Staff", "Samuel"], "department": ["Legal", "Survey"]})
        self.assertEqual((event.actor, event.target_user, event.target_email), (self.mgr, self.staff, "staff@acme.test"))
        self.assertEqual(AccountEvent.objects.filter(action="role_changed").count(), 0)

    def test_role_change_records_before_and_after_as_its_own_event(self):
        ss.update_staff(self.md, self.sales.pk, role="viewer")
        self.assertEqual(self.fresh(self.sales).role, "viewer")
        event = AccountEvent.objects.get(action="role_changed")
        self.assertEqual((event.changes, event.actor), ({"role": ["sales", "viewer"]}, self.md))

    def test_email_username_and_company_are_not_editable_here(self):
        with self.assertRaises(TypeError):
            ss.update_staff(self.md, self.staff.pk, email="x@y.test")
        with self.assertRaises(TypeError):
            ss.update_staff(self.md, self.staff.pk, company=self.other)

    def test_nothing_to_change_is_refused(self):
        with self.assertRaises(ValueError):
            ss.update_staff(self.md, self.staff.pk, first_name="Staff")
        self.assertEqual(AccountEvent.objects.count(), 0)

    def test_manager_cannot_touch_md_or_manager_accounts_or_promote(self):
        for target in (self.md, self.mgr2):
            before = self.snapshot(target)
            with self.assertRaises(PermissionDenied):
                ss.update_staff(self.mgr, target.pk, first_name="Hacked")
            with self.assertRaises(PermissionDenied):
                ss.update_staff(self.mgr, target.pk, role="viewer")
            self.assertEqual(self.snapshot(target), before)
        for role in ("manager", "md"):
            with self.assertRaises(PermissionDenied):
                ss.update_staff(self.mgr, self.staff.pk, role=role)
        self.assertEqual(self.fresh(self.staff).role, "staff")

    def test_md_can_promote_demote_and_manage_another_md(self):
        ss.update_staff(self.md, self.staff.pk, role="manager")
        ss.update_staff(self.md, self.mgr.pk, role="viewer")
        ss.update_staff(self.md, self.md2.pk, first_name="Second")
        ss.update_staff(self.md, self.md2.pk, role="manager")                    # another MD can be demoted
        self.assertEqual((self.fresh(self.staff).role, self.fresh(self.mgr).role, self.fresh(self.md2).role),
                         ("manager", "viewer", "manager"))

    def test_nobody_changes_their_own_role(self):
        for actor in (self.md, self.mgr):
            with self.assertRaises(PermissionDenied):
                ss.update_staff(actor, actor.pk, role="viewer")
        self.assertEqual((self.fresh(self.md).role, self.fresh(self.mgr).role), ("md", "manager"))
        ss.update_staff(self.md, self.md.pk, first_name="Renamed")             # their own name is fine
        self.assertEqual(self.fresh(self.md).first_name, "Renamed")

    def test_staff_role_needs_a_department_but_unrelated_edits_do_not(self):
        with self.assertRaises(ss.StaffValidationError):
            ss.update_staff(self.md, self.sales.pk, role="staff")
        ss.update_staff(self.md, self.sales.pk, role="staff", department=self.legal)
        with self.assertRaises(ss.StaffValidationError):
            ss.update_staff(self.md, self.staff.pk, department=None)
        User.objects.filter(pk=self.staff.pk).update(department=None)             # an old staff record without one
        ss.update_staff(self.md, self.staff.pk, first_name="Still editable")
        with self.assertRaises(ss.StaffValidationError):
            ss.update_staff(self.md, self.staff.pk, department=self.other_dept)

    def test_last_active_md_guard(self):
        with self.assertRaises(ss.StaffStateError):
            ss._guard_last_md([self.md], self.md)
        with self.assertRaises(ss.StaffStateError):
            ss._guard_last_md([], self.md)
        ss._guard_last_md([self.md, self.md2], self.md)                          # two active MDs: fine
        ss._guard_last_md([self.md], self.mgr)                                   # not an MD: nothing to protect

    def test_failure_while_recording_leaves_the_account_unchanged(self):
        before = self.snapshot(self.staff)
        calls = []

        def second_call_fails(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError("boom")
            return AccountEvent.objects.create(company_id=args[0], action=args[1], target_user=kwargs["target_user"])

        with patch("tracker.staff_services.acc.log_account_event", side_effect=second_call_fails):
            with self.assertRaises(RuntimeError):
                ss.update_staff(self.md, self.staff.pk, first_name="Changed", role="sales")
        self.assertEqual(self.snapshot(self.staff), before)
        self.assertEqual(AccountEvent.objects.count(), 0)


class ProtectedAndCompanyTests(Base):
    def setUp(self):
        super().setUp()
        self.kenny = make_user(self.co, "kenny@acme.test", "md", is_staff=True, is_superuser=True)
        self.root = make_user(self.co, "root@acme.test", "staff", self.legal, is_superuser=True)
        self.admin = make_user(self.co, "adm@acme.test", "viewer", is_staff=True)

    def test_django_admin_accounts_are_read_only_for_everyone(self):
        for protected in (self.kenny, self.root, self.admin):
            before = self.snapshot(protected)
            for actor in (self.md, self.md2):
                for call in (
                    lambda: ss.update_staff(actor, protected.pk, first_name="X"),
                    lambda: ss.update_staff(actor, protected.pk, role="viewer"),
                    lambda: ss.deactivate(actor, protected.pk),
                    lambda: ss.reactivate(actor, protected.pk),
                    lambda: ss.issue_link_for(actor, protected.pk, acc.SETUP),
                    lambda: ss.issue_link_for(actor, protected.pk, acc.RESET),
                ):
                    with self.assertRaises(PermissionDenied, msg=protected.username):
                        call()
            self.assertEqual(self.snapshot(protected), before)
        self.assertEqual(AccountEvent.objects.count(), 0)
        self.assertFalse(ss.may_manage(self.md, self.kenny))
        self.assertTrue(ss.may_manage(self.md, self.staff))

    def test_other_companies_accounts_are_out_of_reach_for_every_action(self):
        before = self.snapshot(self.ostaff, self.omd)
        for target in (self.ostaff, self.omd):
            for call in (
                lambda: ss.update_staff(self.md, target.pk, first_name="X"),
                lambda: ss.deactivate(self.md, target.pk),
                lambda: ss.reactivate(self.md, target.pk),
                lambda: ss.issue_link_for(self.md, target.pk, acc.RESET),
                lambda: ss.account_history(self.md, target),
            ):
                with self.assertRaises(PermissionDenied):
                    call()
        with self.assertRaises(PermissionDenied):                            # an id that does not exist at all: same answer
            ss.update_staff(self.md, 987654, first_name="X")
        self.assertEqual(self.snapshot(self.ostaff, self.omd), before)
        self.assertEqual(AccountEvent.objects.count(), 0)

    def test_an_actor_deactivated_or_demoted_in_the_meantime_can_no_longer_act(self):
        stale_md = self.fresh(self.md)                                       # looks like an active MD in memory
        User.objects.filter(pk=self.md.pk).update(is_active=False)
        with self.assertRaises(PermissionDenied):
            ss.deactivate(stale_md, self.staff.pk)
        User.objects.filter(pk=self.md.pk).update(is_active=True, role="viewer")
        with self.assertRaises(PermissionDenied):
            self.new(stale_md)
        self.assertTrue(self.fresh(self.staff).is_active)


class DeactivateReactivateTests(Base):
    def test_deactivate_and_reactivate_record_events_and_kill_links(self):
        link = acc.issue_link(self.staff, acc.SETUP)
        result = ss.deactivate(self.md, self.staff.pk, reason="Left the company")
        staff = self.fresh(self.staff)
        self.assertFalse(staff.is_active)
        self.assertEqual(result.open_obligations, 0)
        with self.assertRaises(acc.InvalidLink):
            acc.complete_password_link(link.token, acc.SETUP, "Tr1cky-Horse-Battery-42")
        event = AccountEvent.objects.get(action="deactivated")
        self.assertEqual((event.changes, event.actor, event.target_user), ({"is_active": [True, False]}, self.md, self.staff))
        self.assertIn("Left the company", event.note)
        ss.reactivate(self.md, self.staff.pk)
        self.assertTrue(self.fresh(self.staff).is_active)
        with self.assertRaises(acc.InvalidLink):                              # reactivation does not revive old links
            acc.complete_password_link(link.token, acc.SETUP, "Tr1cky-Horse-Battery-42")
        self.assertEqual(AccountEvent.objects.get(action="reactivated").changes, {"is_active": [False, True]})

    def test_a_link_stays_dead_even_if_someone_switches_the_account_back_on_outside_the_api(self):
        link = acc.issue_link(self.staff, acc.SETUP)
        ss.deactivate(self.md, self.staff.pk)
        User.objects.filter(pk=self.staff.pk).update(is_active=True)           # e.g. a Django admin tick-box
        with self.assertRaises(acc.InvalidLink):
            acc.complete_password_link(link.token, acc.SETUP, "Tr1cky-Horse-Battery-42")

    def test_state_errors(self):
        with self.assertRaises(ss.StaffStateError):
            ss.reactivate(self.md, self.staff.pk)
        ss.deactivate(self.md, self.staff.pk)
        with self.assertRaises(ss.StaffStateError):
            ss.deactivate(self.md, self.staff.pk)

    def test_nobody_deactivates_themselves(self):
        for actor in (self.md, self.mgr):
            with self.assertRaises(PermissionDenied):
                ss.deactivate(actor, actor.pk)
            self.assertTrue(self.fresh(actor).is_active)

    def test_manager_cannot_deactivate_or_reactivate_md_or_manager(self):
        User.objects.filter(pk=self.mgr2.pk).update(is_active=False)
        for target in (self.md, self.mgr2):
            with self.assertRaises(PermissionDenied):
                ss.deactivate(self.mgr, target.pk)
            with self.assertRaises(PermissionDenied):
                ss.reactivate(self.mgr, target.pk)
        self.assertTrue(self.fresh(self.md).is_active)
        self.assertFalse(self.fresh(self.mgr2).is_active)

    def test_reactivating_an_md_needs_an_actor_who_manages_mds(self):
        ss.deactivate(self.md, self.md2.pk)
        with self.assertRaises(PermissionDenied):
            ss.reactivate(self.mgr, self.md2.pk)
        ss.reactivate(self.md, self.md2.pk)
        self.assertTrue(self.fresh(self.md2).is_active)

    def test_owned_obligations_are_counted_but_never_touched(self):
        open_a = self.make_obligation(self.staff, "not_started")
        open_b = self.make_obligation(self.staff, "awaiting_external")
        self.make_obligation(self.staff, "completed")
        self.make_obligation(self.staff, "in_progress", sale_status="cancelled")      # cancelled sale: not active work
        self.make_obligation(self.sales, "not_started")                               # someone else's
        events_before = ObligationEvent.objects.count()
        self.assertEqual(ss.open_obligation_count(self.staff), 2)
        result = ss.deactivate(self.md, self.staff.pk)
        self.assertEqual(result.open_obligations, 2)
        self.assertIn("2 open obligation(s) remain", AccountEvent.objects.get(action="deactivated").note)
        self.assertEqual(Obligation.objects.filter(owner=self.staff).count(), 4)       # nothing reassigned or deleted
        self.assertEqual(Obligation.objects.get(pk=open_a.pk).owner, self.staff)
        self.assertEqual(Obligation.objects.get(pk=open_b.pk).status, "awaiting_external")
        self.assertEqual(ObligationEvent.objects.count(), events_before)

    def test_reason_length_is_limited(self):
        with self.assertRaises(ss.StaffValidationError):
            ss.deactivate(self.md, self.staff.pk, reason="x" * 501)
        self.assertTrue(self.fresh(self.staff).is_active)


class LinkAndHistoryTests(Base):
    def test_manager_cannot_issue_links_for_md_or_manager(self):
        before = self.snapshot(self.md, self.mgr2)
        for target in (self.md, self.mgr2):
            for purpose in (acc.SETUP, acc.RESET):
                with self.assertRaises(PermissionDenied):
                    ss.issue_link_for(self.mgr, target.pk, purpose)
        self.assertEqual(self.snapshot(self.md, self.mgr2), before)

    def test_md_can_issue_links_for_another_md_and_for_staff(self):
        for target in (self.md2, self.staff):
            outcome = ss.issue_link_for(self.md, target.pk, acc.SETUP)
            self.assertEqual(outcome.status, "revealed")
        self.assertEqual(AccountEvent.objects.filter(action="setup_link_issued", actor=self.md).count(), 2)

    def test_inactive_account_gets_no_link(self):
        ss.deactivate(self.md, self.staff.pk)
        with self.assertRaises(acc.AccountLinkError):
            ss.issue_link_for(self.md, self.staff.pk, acc.RESET)

    def test_history_is_visible_only_to_roles_that_manage_the_account(self):
        ss.update_staff(self.md, self.staff.pk, first_name="Changed")
        ss.update_staff(self.md, self.md2.pk, first_name="Changed")
        self.assertEqual([e.action for e in ss.account_history(self.mgr, self.staff)], ["profile_updated"])
        self.assertEqual(ss.account_history(self.md, self.md2).count(), 1)
        for target in (self.md2, self.mgr2):
            with self.assertRaises(PermissionDenied):
                ss.account_history(self.mgr, target)

    def test_history_is_company_scoped(self):
        ss.update_staff(self.omd, self.ostaff.pk, first_name="Changed")
        self.assertEqual(ss.account_history(self.omd, self.ostaff).count(), 1)
        with self.assertRaises(PermissionDenied):
            ss.account_history(self.md, self.ostaff)


class DatabaseUniquenessTests(Base):
    def test_the_database_refuses_a_second_account_with_the_same_email_ignoring_case(self):
        User.objects.create_user("d1", email="Dup@Acme.Test", company=self.co)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                User.objects.create_user("d2", email="dup@acme.test", company=self.other)

    def test_blank_emails_are_allowed_many_times(self):
        for name in ("b1", "b2", "b3"):
            User.objects.create_user(name, company=self.co)


@skipUnless(connection.vendor == "postgresql", "needs PostgreSQL row locks")
class ConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.co = Company.objects.create(name="Race Co")
        self.a = make_user(self.co, "a@race.test", "md")
        self.b = make_user(self.co, "b@race.test", "md")

    def run_both(self, jobs):
        barrier, results = threading.Barrier(len(jobs)), {}

        def worker(name, job):
            try:
                barrier.wait()
                job()
                results[name] = "ok"
            except PermissionDenied:
                results[name] = "denied"
            except ss.StaffStateError:
                results[name] = "state"
            except ss.StaffValidationError:
                results[name] = "invalid"
            finally:
                connection.close()

        threads = [threading.Thread(target=worker, args=(n, j)) for n, j in jobs.items()]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return results

    def test_two_mds_deactivating_each_other_cannot_leave_the_company_without_one(self):
        results = self.run_both({"a": lambda: ss.deactivate(self.a, self.b.pk),
                                 "b": lambda: ss.deactivate(self.b, self.a.pk)})
        self.assertEqual(sorted(results.values()).count("ok"), 1, results)
        self.assertEqual(User.objects.filter(company=self.co, role="md", is_active=True).count(), 1)

    def test_two_mds_demoting_each_other_cannot_leave_the_company_without_one(self):
        results = self.run_both({"a": lambda: ss.update_staff(self.a, self.b.pk, role="viewer"),
                                 "b": lambda: ss.update_staff(self.b, self.a.pk, role="viewer")})
        self.assertEqual(sorted(results.values()).count("ok"), 1, results)
        self.assertEqual(User.objects.filter(company=self.co, role="md", is_active=True).count(), 1)

    def test_two_requests_creating_the_same_email_create_one_account(self):
        make = lambda: ss.create_staff(self.a, email="same@race.test", first_name="S", last_name="T", role="viewer")  # noqa: E731
        results = self.run_both({"one": make, "two": make})
        self.assertEqual(sorted(results.values()), ["invalid", "ok"], results)
        self.assertEqual(User.objects.filter(email="same@race.test").count(), 1)
