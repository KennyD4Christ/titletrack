import unittest
from datetime import timedelta

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from api.permissions import MANAGER, MATRIX, MD
from tracker import document_services as catalogue
from tracker import sale_document_services as docs
from tracker import services
from tracker.models import (
    Buyer, Company, Department, DocumentType, Estate, Obligation, ObligationEvent, ObligationType, Plot, Sale,
    SaleDocument, SaleEvent, User,
)

ROW_KEYS = {
    "id", "sale", "sale_status", "buyer_name", "plot_number", "estate_name", "document_type", "name",
    "catalogue_name", "state", "display_status", "is_overdue", "issued_on", "reference", "issued_by_name",
    "issued_at", "not_applicable_reason", "not_applicable_by_name", "not_applicable_at", "obligation",
    "obligation_status", "due_date", "owner_name", "added_by_name", "added_at", "can",
}
CAN_KEYS = {"issue", "mark_not_applicable", "correct_issuance"}


def _sales_desk_available():
    try:
        reverse("sales-desk-detail", args=[1])
        return True
    except NoReverseMatch:
        return False


class SaleDocApiBase(APITestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.a = Company.objects.create(name="A Homes")
        self.b = Company.objects.create(name="B Homes")
        self.legal = Department.objects.create(company=self.a, name="Legal")
        make = lambda name, role, company=self.a: User.objects.create_user(name, password="x", company=company, role=role)
        self.md, self.manager = make("md", "md"), make("manager", "manager")
        self.staff, self.sales, self.viewer = make("staff", "staff"), make("sales", "sales"), make("viewer", "viewer")
        self.b_manager = make("b_manager", "manager", self.b)
        self.outsider = User.objects.create_user("outsider", password="x", role="staff")       # no company
        self.estates = {c.pk: Estate.objects.create(company=c, name="Green Valley") for c in (self.a, self.b)}
        self.n = 0

    # ---- requests ----

    def call(self, user, method, url, payload=None, **params):
        self.client.force_authenticate(user)
        if method == "get":
            return self.client.get(url, params)
        return getattr(self.client, method)(url, payload or {}, format="json")

    def list_url(self):
        return reverse("sale-document-list")

    def detail(self, doc):
        return reverse("sale-document-detail", args=[doc.pk])

    def act(self, name, doc):
        return reverse(f"sale-document-{name}", args=[doc.pk])

    def ids(self, user=None, **params):
        response = self.call(user or self.manager, "get", self.list_url(), page_size=200, **params)
        self.assertEqual(response.status_code, 200, response.data)
        return {row["id"] for row in response.data["results"]}

    # ---- fixtures ----

    def sale(self, company=None, paid=400, managed=True):
        company = company or self.a
        self.n += 1
        plot = Plot.objects.create(company=company, estate=self.estates[company.pk], plot_number=f"P-{self.n}")
        buyer = Buyer.objects.create(company=company, full_name=f"Buyer {self.n}")
        sale = services.create_sale(company, plot, buyer, 1000, self.today - timedelta(days=40), paid,
                                    self.today - timedelta(days=40))
        if managed:
            sale.obligations.all().delete()
            Sale.objects.filter(pk=sale.pk).update(documents_managed=True)
        return Sale.objects.get(pk=sale.pk)

    def entry(self, name="Survey Plan", company=None, actor=None, **extra):
        company = company or self.a
        return catalogue.create_document_type(company, actor or (self.manager if company == self.a else self.b_manager),
                                              name=name, **extra)

    def legacy_entry(self, obligation_type):
        entry = DocumentType.objects.create(company=self.a, name=obligation_type.name)
        ObligationType.objects.filter(pk=obligation_type.pk).update(catalogue_entry=entry, is_current=True)
        return entry

    def owed(self, sale=None, entry=None, name="Survey Plan"):
        return docs.add_document(sale or self.sale(), self.manager, entry or self.entry(name), "owed")

    def issued(self, sale=None, entry=None, name="Survey Plan"):
        doc = self.owed(sale, entry, name)
        return docs.issue_document(doc, self.manager, self.today - timedelta(days=2), reference="REF-1")

    def counts(self):
        return (SaleDocument.objects.count(), Obligation.objects.count(), SaleEvent.objects.count(),
                ObligationEvent.objects.count())


class ReadTests(SaleDocApiBase):
    def test_md_and_manager_can_read_and_every_other_caller_is_refused(self):
        doc = self.owed()
        for user in (self.md, self.manager):
            self.assertEqual(self.call(user, "get", self.list_url()).status_code, 200, user.role)
            self.assertEqual(self.call(user, "get", self.detail(doc)).status_code, 200, user.role)
        for user in (self.staff, self.sales, self.viewer, self.outsider):
            self.assertEqual(self.call(user, "get", self.list_url()).status_code, 403, user.role)
            self.assertEqual(self.call(user, "get", self.detail(doc)).status_code, 403, user.role)
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(self.list_url()).status_code, (401, 403))

    def test_the_response_has_exactly_the_documented_fields_and_values(self):
        entry = self.entry("Survey Plan", sla_days=45, default_department=self.legal)
        doc = self.owed(entry=entry)
        row = self.call(self.manager, "get", self.detail(doc)).data
        self.assertEqual(set(row), ROW_KEYS)
        self.assertEqual(set(row["can"]), CAN_KEYS)
        self.assertEqual((row["name"], row["catalogue_name"], row["state"], row["display_status"], row["sale_status"]),
                         ("Survey Plan", "Survey Plan", "owed", "outstanding", "active"))
        self.assertEqual((row["obligation"], row["obligation_status"], row["owner_name"], row["is_overdue"]),
                         (doc.obligation_id, "not_started", None, False))
        self.assertEqual((row["buyer_name"], row["estate_name"], row["added_by_name"], row["issued_on"]),
                         (doc.sale.buyer.full_name, "Green Valley", "manager", None))
        self.assertEqual(row["due_date"], (self.today + timedelta(days=45)).isoformat())

    def test_the_display_status_follows_the_obligation_and_only_issuing_makes_it_issued(self):
        doc = self.owed()
        status = lambda: self.call(self.manager, "get", self.detail(doc)).data
        self.assertEqual(status()["display_status"], "outstanding")
        services.update_obligation(doc.obligation, self.manager, status="in_progress")
        self.assertEqual(status()["display_status"], "in_progress")
        services.update_obligation(doc.obligation, self.manager, status="awaiting_external")
        self.assertEqual(status()["display_status"], "in_progress")
        services.update_obligation(doc.obligation, self.manager, status="completed")
        ready = status()
        self.assertEqual((ready["display_status"], ready["state"]), ("ready_to_issue", "owed"))
        docs.issue_document(doc, self.manager, self.today)
        self.assertEqual(status()["display_status"], "issued")
        other = self.owed(entry=self.entry("Other Doc"))
        docs.mark_not_applicable(other, self.manager, "Not required")
        self.assertEqual(self.call(self.manager, "get", self.detail(other)).data["display_status"], "not_required")

    def test_overdue_is_a_flag_on_open_owed_documents_only(self):
        doc = self.owed()
        Obligation.objects.filter(pk=doc.obligation_id).update(due_date=self.today - timedelta(days=3))
        self.assertTrue(self.call(self.manager, "get", self.detail(doc)).data["is_overdue"])
        services.update_obligation(doc.obligation, self.manager, status="completed")
        self.assertFalse(self.call(self.manager, "get", self.detail(doc)).data["is_overdue"])

    def test_the_per_document_can_follows_role_state_and_sale(self):
        owed, issued = self.owed(), self.issued(entry=self.entry("Second Doc"))
        skipped = self.owed(entry=self.entry("Third Doc"))
        docs.mark_not_applicable(skipped, self.manager, "Not required")
        cancelled = self.owed(entry=self.entry("Fourth Doc"))
        services.cancel_sale(cancelled.sale, self.manager, "defaulted")
        stuck = self.owed(entry=self.entry("Fifth Doc"))
        Obligation.objects.filter(pk=stuck.obligation_id).update(status="not_applicable")
        expected = {owed: (True, True, False), issued: (False, False, True), skipped: (False, False, False),
                    cancelled: (False, False, False), stuck: (False, False, False)}
        for user in (self.md, self.manager):
            for doc, (issue, na, correct) in expected.items():
                can = self.call(user, "get", self.detail(doc)).data["can"]
                self.assertEqual(can, {"issue": issue, "mark_not_applicable": na, "correct_issuance": correct},
                                 f"{user.role} {doc.name_snapshot}")

    def test_company_isolation(self):
        mine, theirs = self.owed(), docs.add_document(
            self.sale(self.b), self.b_manager, self.entry("Deed", self.b), "owed", )
        self.assertEqual(self.ids(), {mine.pk})
        self.assertEqual(self.ids(self.b_manager), {theirs.pk})
        before = self.counts()
        self.assertEqual(self.call(self.manager, "get", self.detail(theirs)).status_code, 404)
        self.assertEqual(self.call(self.manager, "post", self.act("issue", theirs),
                                   {"issued_on": self.today.isoformat()}).status_code, 404)
        self.assertEqual(self.call(self.manager, "post", self.act("mark-not-applicable", theirs),
                                   {"reason": "hijack"}).status_code, 404)
        self.assertEqual(self.call(self.manager, "post", self.act("correct-issuance", theirs),
                                   {"reason": "hijack", "reference": "X"}).status_code, 404)
        self.assertEqual(self.counts(), before)
        self.assertEqual(SaleDocument.objects.get(pk=theirs.pk).state, "owed")

    def test_listing_costs_a_fixed_number_of_queries(self):
        for i in range(2):
            self.owed(entry=self.entry(f"Doc {i}"))
        self.client.force_authenticate(self.manager)
        with CaptureQueriesContext(connection) as small:
            self.client.get(self.list_url(), {"page_size": 200})
        for i in range(10):
            self.owed(entry=self.entry(f"Extra {i}"))
        with CaptureQueriesContext(connection) as large:
            response = self.client.get(self.list_url(), {"page_size": 200})
        self.assertEqual(response.data["count"], 12)
        self.assertEqual(len(small), len(large))


class FilterTests(SaleDocApiBase):
    def setUp(self):
        super().setUp()
        self.alpha, self.beta, self.gamma = self.entry("Alpha"), self.entry("Beta"), self.entry("Gamma")
        self.s1, self.s2 = self.sale(), self.sale()
        self.outstanding = self.owed(self.s1, self.alpha)
        self.ready = self.owed(self.s1, self.beta)
        services.update_obligation(self.ready.obligation, self.manager, status="completed")
        self.issued_doc = docs.add_document(self.s2, self.manager, self.alpha, "issued", issued_on=self.today)
        self.skipped = self.owed(self.s2, self.beta)
        docs.mark_not_applicable(self.skipped, self.manager, "Not required")
        self.late = self.owed(self.s2, self.gamma)
        Obligation.objects.filter(pk=self.late.obligation_id).update(due_date=self.today - timedelta(days=5))
        self.everything = {self.outstanding.pk, self.ready.pk, self.issued_doc.pk, self.skipped.pk, self.late.pk}

    def test_no_filter_lists_every_document_of_active_sales(self):
        self.assertEqual(self.ids(), self.everything)

    def test_by_sale_state_and_document_type(self):
        self.assertEqual(self.ids(sale=self.s1.pk), {self.outstanding.pk, self.ready.pk})
        self.assertEqual(self.ids(state="issued"), {self.issued_doc.pk})
        self.assertEqual(self.ids(state="not_applicable"), {self.skipped.pk})
        self.assertEqual(self.ids(state="owed"), {self.outstanding.pk, self.ready.pk, self.late.pk})
        self.assertEqual(self.ids(document_type=self.alpha.pk), {self.outstanding.pk, self.issued_doc.pk})

    def test_ready_to_issue_and_overdue(self):
        self.assertEqual(self.ids(ready_to_issue="true"), {self.ready.pk})
        self.assertEqual(self.ids(ready_to_issue="false"), self.everything - {self.ready.pk})
        self.assertEqual(self.ids(overdue="true"), {self.late.pk})
        self.assertEqual(self.ids(overdue="false"), self.everything - {self.late.pk})

    def test_filters_combine(self):
        self.assertEqual(self.ids(sale=self.s2.pk, state="owed"), {self.late.pk})
        self.assertEqual(self.ids(state="owed", ready_to_issue="false"), {self.outstanding.pk, self.late.pk})
        self.assertEqual(self.ids(document_type=self.beta.pk, sale=self.s1.pk), {self.ready.pk})
        self.assertEqual(self.ids(state="issued", overdue="true"), set())

    def test_cancelled_sales_are_left_out_unless_asked_for(self):
        services.cancel_sale(self.s2, self.manager, "defaulted")
        self.assertEqual(self.ids(), {self.outstanding.pk, self.ready.pk})
        self.assertEqual(self.ids(include_cancelled="true"), self.everything)
        self.assertEqual(self.ids(sale=self.s2.pk), {self.issued_doc.pk, self.skipped.pk, self.late.pk})
        self.assertEqual(self.ids(overdue="true"), set())                  # a cancelled sale's work is never overdue
        self.assertEqual(self.call(self.manager, "get", self.detail(self.late)).status_code, 200)

    def test_bad_filter_values_are_refused_with_the_parameter_named(self):
        for params in ({"sale": "abc"}, {"document_type": "x1"}, {"state": "bogus"}, {"ready_to_issue": "maybe"},
                       {"overdue": "2"}, {"include_cancelled": "perhaps"}):
            response = self.call(self.manager, "get", self.list_url(), **params)
            self.assertEqual(response.status_code, 400, params)
            self.assertIn(next(iter(params)), response.data, params)


class CreateTests(SaleDocApiBase):
    def create(self, user=None, **payload):
        return self.call(user or self.manager, "post", self.list_url(), payload)

    def test_adds_an_owed_document_and_creates_its_obligation(self):
        sale, entry = self.sale(), self.entry("Survey Plan", sla_days=30)
        response = self.create(sale=sale.pk, document_type=entry.pk, state="owed")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(set(response.data), ROW_KEYS)
        self.assertEqual((response.data["state"], response.data["display_status"], response.data["name"]),
                         ("owed", "outstanding", "Survey Plan"))
        self.assertEqual(Obligation.objects.get(pk=response.data["obligation"]).sale_id, sale.pk)
        event = SaleEvent.objects.filter(sale=sale, action="document_added").get()
        self.assertEqual(event.actor, self.manager)

    def test_adds_an_already_issued_document(self):
        sale, entry = self.sale(), self.entry()
        response = self.create(sale=sale.pk, document_type=entry.pk, state="issued",
                               issued_on=(self.today - timedelta(days=9)).isoformat(), reference="REF 9")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual((response.data["state"], response.data["reference"], response.data["obligation"],
                          response.data["display_status"]), ("issued", "REF 9", None, "issued"))
        self.assertEqual(response.data["issued_by_name"], "manager")

    def test_a_client_supplied_obligation_is_ignored(self):
        sale, entry = self.sale(), self.entry("Survey Plan")
        stray = Obligation.objects.create(
            company=self.a, sale=self.sale(), obligation_type=catalogue.current_work_type(entry),
            started_on=self.today, due_date=self.today)
        response = self.create(sale=sale.pk, document_type=entry.pk, state="owed", obligation=stray.pk)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertNotEqual(response.data["obligation"], stray.pk)
        self.assertFalse(SaleDocument.objects.filter(obligation=stray).exists())

    def test_missing_and_invalid_fields_are_named(self):
        response = self.create()
        self.assertEqual(response.status_code, 400)
        self.assertTrue({"sale", "document_type", "state"} <= set(response.data))
        sale, entry = self.sale(), self.entry()
        for bad_state in ("not_applicable", "bogus", ""):
            response = self.create(sale=sale.pk, document_type=entry.pk, state=bad_state)
            self.assertEqual(response.status_code, 400, bad_state)
            self.assertIn("state", response.data)
        self.assertEqual(SaleDocument.objects.count(), 0)

    def test_an_issued_document_needs_a_valid_date_and_a_short_enough_reference(self):
        sale, entry = self.sale(), self.entry()
        base = dict(sale=sale.pk, document_type=entry.pk, state="issued")
        for extra in ({}, {"issued_on": None}, {"issued_on": (self.today + timedelta(days=9)).isoformat()},
                      {"issued_on": "1980-01-01"}, {"issued_on": "31/02/2026"}, {"issued_on": "soon"}):
            response = self.create(**base, **extra)
            self.assertEqual(response.status_code, 400, extra)
            self.assertIn("issued_on", response.data, extra)
        response = self.create(**base, issued_on=self.today.isoformat(), reference="r" * 121)
        self.assertEqual((response.status_code, "reference" in response.data), (400, True))
        self.assertEqual(SaleDocument.objects.count(), 0)

    def test_an_owed_document_refuses_an_issue_date(self):
        sale, entry = self.sale(), self.entry()
        response = self.create(sale=sale.pk, document_type=entry.pk, state="owed", issued_on=self.today.isoformat())
        self.assertEqual(response.status_code, 400)
        self.assertIn("issued_on", response.data)

    def test_wrong_state_is_a_409_and_changes_nothing(self):
        sale, entry = self.sale(), self.entry()
        inactive = self.entry("Inactive Doc")
        catalogue.set_document_type_active(inactive, self.manager, False)
        cancelled = self.sale()
        services.cancel_sale(cancelled, self.manager, "defaulted")
        self.create(sale=sale.pk, document_type=entry.pk, state="owed")
        before = self.counts()
        cases = {   # label: (payload, the key the message is filed under)
            "duplicate": (dict(sale=sale.pk, document_type=entry.pk, state="owed"), "document_type"),
            "inactive": (dict(sale=sale.pk, document_type=inactive.pk, state="owed"), "document_type"),
            "cancelled": (dict(sale=cancelled.pk, document_type=entry.pk, state="owed"), "detail"),
        }
        for label, (payload, key) in cases.items():
            response = self.create(**payload)
            self.assertEqual(response.status_code, 409, label)
            self.assertIn(key, response.data, label)
        self.assertEqual(self.counts(), before)

    def test_forged_sale_and_document_type_ids_are_refused(self):
        foreign_sale = self.sale(self.b)
        foreign_entry = self.entry("Foreign Doc", self.b)
        sale, entry = self.sale(), self.entry()
        before = self.counts()
        response = self.create(sale=foreign_sale.pk, document_type=entry.pk, state="owed")
        self.assertEqual((response.status_code, "sale" in response.data), (400, True))
        response = self.create(sale=sale.pk, document_type=foreign_entry.pk, state="owed")
        self.assertEqual((response.status_code, "document_type" in response.data), (400, True))
        response = self.create(sale=987654, document_type=987654, state="owed")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.counts(), before)

    def test_legacy_sales_adopt_open_obligations_and_refuse_the_rest(self):
        deed = ObligationType.objects.create(company=self.a, name="Deed", trigger="on_full_payment", sla_days=90)
        entry = self.legacy_entry(deed)
        open_sale = self.sale(paid=1000, managed=False)                      # the Deed obligation is open
        existing = open_sale.obligations.get(obligation_type=deed)
        response = self.create(sale=open_sale.pk, document_type=entry.pk, state="owed")
        self.assertEqual((response.status_code, response.data["obligation"]), (201, existing.pk))
        self.assertEqual(Obligation.objects.get(pk=existing.pk).status, "not_started")
        done_sale = self.sale(paid=1000, managed=False)
        services.update_obligation(done_sale.obligations.get(obligation_type=deed), self.manager, status="completed")
        issued_sale = self.sale(paid=1000, managed=False)
        before = self.counts()
        refused = self.create(sale=done_sale.pk, document_type=entry.pk, state="owed")
        self.assertEqual(refused.status_code, 409)
        self.assertIn("already completed", refused.data["document_type"][0])
        direct = self.create(sale=issued_sale.pk, document_type=entry.pk, state="issued", issued_on=self.today.isoformat())
        self.assertEqual(direct.status_code, 409)
        self.assertIn("owed first", direct.data["state"][0])
        self.assertEqual(self.counts(), before)

    def test_other_roles_cannot_create(self):
        sale, entry = self.sale(), self.entry()
        before = self.counts()
        for user in (self.staff, self.sales, self.viewer, self.outsider):
            response = self.create(user, sale=sale.pk, document_type=entry.pk, state="owed")
            self.assertEqual(response.status_code, 403, user.role)
        self.assertEqual(self.counts(), before)


class IssueActionTests(SaleDocApiBase):
    def issue(self, doc, user=None, **payload):
        return self.call(user or self.md, "post", self.act("issue", doc), payload)

    def test_issues_a_document_and_closes_its_obligation(self):
        doc = self.owed()
        when = (self.today - timedelta(days=4)).isoformat()
        response = self.issue(doc, issued_on=when, reference="  TRF  1 ")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual((response.data["state"], response.data["issued_on"], response.data["reference"],
                          response.data["issued_by_name"], response.data["display_status"]),
                         ("issued", when, "TRF 1", "md", "issued"))
        self.assertEqual(response.data["can"], {"issue": False, "mark_not_applicable": False, "correct_issuance": True})
        self.assertEqual(Obligation.objects.get(pk=doc.obligation_id).status, "completed")
        event = SaleEvent.objects.filter(sale=doc.sale, action="document_issued").get()
        self.assertEqual((event.actor, event.changes["state"]), (self.md, ["owed", "issued"]))

    def test_completing_the_obligation_alone_does_not_issue_it(self):
        doc = self.owed()
        services.update_obligation(doc.obligation, self.staff, status="completed")
        row = self.call(self.manager, "get", self.detail(doc)).data
        self.assertEqual((row["state"], row["display_status"]), ("owed", "ready_to_issue"))

    def test_the_date_is_required_and_checked(self):
        doc = self.owed()
        for payload in ({}, {"issued_on": ""}, {"issued_on": "31/02/2026"}, {"issued_on": "soon"},
                        {"issued_on": (self.today + timedelta(days=9)).isoformat()}, {"issued_on": "1980-01-01"}):
            response = self.issue(doc, **payload)
            self.assertEqual(response.status_code, 400, payload)
            self.assertIn("issued_on", response.data, payload)
        long_reference = self.issue(doc, issued_on=self.today.isoformat(), reference="r" * 121)
        self.assertEqual((long_reference.status_code, "reference" in long_reference.data), (400, True))
        self.assertEqual(SaleDocument.objects.get(pk=doc.pk).state, "owed")

    def test_wrong_states_are_409(self):
        issued, owed_then_skipped, cancelled = self.issued(), self.owed(entry=self.entry("Other Doc")), \
            self.owed(entry=self.entry("Third Doc"))
        docs.mark_not_applicable(owed_then_skipped, self.manager, "Not required")
        services.cancel_sale(cancelled.sale, self.manager, "defaulted")
        for doc in (issued, owed_then_skipped, cancelled):
            self.assertEqual(self.issue(doc, issued_on=self.today.isoformat()).status_code, 409)

    def test_other_roles_cannot_issue_and_nothing_changes(self):
        doc = self.owed()
        before = self.counts()
        for user in (self.staff, self.sales, self.viewer, self.outsider):
            self.assertEqual(self.issue(doc, user, issued_on=self.today.isoformat()).status_code, 403, user.role)
        self.assertEqual((SaleDocument.objects.get(pk=doc.pk).state, self.counts()), ("owed", before))


class NotApplicableActionTests(SaleDocApiBase):
    def skip(self, doc, user=None, **payload):
        return self.call(user or self.md, "post", self.act("mark-not-applicable", doc), payload)

    def test_marks_a_document_not_applicable_and_closes_its_open_obligation(self):
        doc = self.owed()
        response = self.skip(doc, reason="  Buyer already holds it ")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual((response.data["state"], response.data["not_applicable_reason"],
                          response.data["not_applicable_by_name"], response.data["display_status"]),
                         ("not_applicable", "Buyer already holds it", "md", "not_required"))
        self.assertEqual(Obligation.objects.get(pk=doc.obligation_id).status, "not_applicable")
        self.assertTrue(SaleEvent.objects.filter(sale=doc.sale, action="document_not_applicable", actor=self.md).exists())

    def test_a_completed_obligation_stays_completed(self):
        doc = self.owed()
        services.update_obligation(doc.obligation, self.manager, status="completed")
        self.assertEqual(self.skip(doc, reason="Not required").status_code, 200)
        self.assertEqual(Obligation.objects.get(pk=doc.obligation_id).status, "completed")

    def test_a_reason_of_at_least_three_characters_is_required(self):
        doc = self.owed()
        for payload in ({}, {"reason": ""}, {"reason": "   "}, {"reason": "ab"}, {"reason": " ab "}):
            response = self.skip(doc, **payload)
            self.assertEqual(response.status_code, 400, payload)
            self.assertIn("reason", response.data, payload)
        self.assertEqual(SaleDocument.objects.get(pk=doc.pk).state, "owed")

    def test_wrong_states_are_409_and_other_roles_are_refused(self):
        issued, twice = self.issued(), self.owed(entry=self.entry("Other Doc"))
        self.assertEqual(self.skip(twice, reason="Not required").status_code, 200)
        self.assertEqual(self.skip(twice, reason="Not required").status_code, 409)
        self.assertEqual(self.skip(issued, reason="Not required").status_code, 409)
        open_doc = self.owed(entry=self.entry("Third Doc"))
        for user in (self.staff, self.sales, self.viewer, self.outsider):
            self.assertEqual(self.skip(open_doc, user, reason="Not required").status_code, 403, user.role)
        self.assertEqual(SaleDocument.objects.get(pk=open_doc.pk).state, "owed")


class CorrectIssuanceActionTests(SaleDocApiBase):
    def setUp(self):
        super().setUp()
        self.doc = self.issued()                                          # issued 2 days ago, reference REF-1
        self.original = SaleDocument.objects.get(pk=self.doc.pk)

    def correct(self, user=None, doc=None, **payload):
        return self.call(user or self.md, "post", self.act("correct-issuance", doc or self.doc), payload)

    def test_corrects_the_date_and_reference_and_records_before_and_after(self):
        new_day = (self.today - timedelta(days=8)).isoformat()
        response = self.correct(reason="Typed the wrong date", issued_on=new_day, reference="REF-2")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual((response.data["issued_on"], response.data["reference"]), (new_day, "REF-2"))
        self.assertEqual((response.data["issued_by_name"], response.data["state"]), ("manager", "issued"))
        event = SaleEvent.objects.filter(sale=self.doc.sale, action="document_issuance_corrected").get()
        self.assertEqual(event.actor, self.md)                                      # who corrected it
        self.assertEqual(event.changes, {"issued_on": [self.original.issued_on.isoformat(), new_day],
                                         "reference": ["REF-1", "REF-2"]})
        self.assertIn("Typed the wrong date", event.note)
        refreshed = SaleDocument.objects.get(pk=self.doc.pk)
        self.assertEqual((refreshed.issued_by, refreshed.issued_at), (self.original.issued_by, self.original.issued_at))

    def test_a_reference_can_be_cleared(self):
        response = self.correct(reason="No reference exists", reference="")
        self.assertEqual((response.status_code, response.data["reference"]), (200, ""))

    def test_the_reason_is_required_and_there_must_be_a_real_change(self):
        for payload in ({"reference": "X"}, {"reason": "", "reference": "X"}, {"reason": "ab", "reference": "X"}):
            response = self.correct(**payload)
            self.assertEqual(response.status_code, 400, payload)
            self.assertIn("reason", response.data, payload)
        for payload in ({"reason": "Checked it"}, {"reason": "Checked it", "reference": "REF-1"},
                        {"reason": "Checked it", "issued_on": self.original.issued_on.isoformat()}):
            response = self.correct(**payload)
            self.assertEqual(response.status_code, 400, payload)
            self.assertIn("Nothing to change", str(response.data), payload)
        self.assertEqual(SaleEvent.objects.filter(action="document_issuance_corrected").count(), 0)

    def test_bad_dates_are_refused(self):
        for bad in ((self.today + timedelta(days=9)).isoformat(), "1980-01-01", "31/02/2026", "soon"):
            response = self.correct(reason="Typed the wrong date", issued_on=bad)
            self.assertEqual(response.status_code, 400, bad)
            self.assertIn("issued_on", response.data, bad)

    def test_only_an_issued_document_can_be_corrected(self):
        owed = self.owed(entry=self.entry("Other Doc"))
        skipped = self.owed(entry=self.entry("Third Doc"))
        docs.mark_not_applicable(skipped, self.manager, "Not required")
        for doc in (owed, skipped):
            self.assertEqual(self.correct(doc=doc, reason="Not issued", reference="X").status_code, 409)
        services.cancel_sale(self.doc.sale, self.manager, "defaulted")
        self.assertEqual(self.correct(reason="Sale is cancelled", reference="X").status_code, 409)

    def test_other_roles_cannot_correct(self):
        before = SaleEvent.objects.count()
        for user in (self.staff, self.sales, self.viewer, self.outsider):
            self.assertEqual(self.correct(user, reason="Typed it wrong", reference="X").status_code, 403, user.role)
        self.assertEqual(SaleEvent.objects.count(), before)
        self.assertEqual(SaleDocument.objects.get(pk=self.doc.pk).reference, "REF-1")


class MethodsAndPermissionsTests(SaleDocApiBase):
    def test_the_new_actions_are_md_and_manager_only(self):
        for action in ("manage_sale_documents", "issue_document"):
            self.assertEqual(MATRIX[action], {MD, MANAGER}, action)
        self.assertEqual(MATRIX["view_sales"], {MD, MANAGER})

    def test_there_is_no_delete_put_or_patch_and_actions_are_post_only(self):
        doc = self.owed()
        for method in ("delete", "put", "patch"):
            self.assertEqual(self.call(self.md, method, self.detail(doc), {}).status_code, 405, method)
        self.assertEqual(self.call(self.md, "post", self.detail(doc), {}).status_code, 405)
        for name in ("issue", "mark-not-applicable", "correct-issuance"):
            self.assertEqual(self.call(self.md, "get", self.act(name, doc)).status_code, 405, name)
        self.assertTrue(SaleDocument.objects.filter(pk=doc.pk).exists())

    def test_every_endpoint_refuses_staff_sales_viewer_and_users_without_a_company(self):
        doc = self.owed()
        endpoints = [("get", self.list_url()), ("get", self.detail(doc)), ("post", self.list_url()),
                     ("post", self.act("issue", doc)), ("post", self.act("mark-not-applicable", doc)),
                     ("post", self.act("correct-issuance", doc))]
        before = self.counts()
        for user in (self.staff, self.sales, self.viewer, self.outsider):
            for method, url in endpoints:
                self.assertEqual(self.call(user, method, url, {}).status_code, 403, f"{user.role} {method} {url}")
        self.assertEqual(self.counts(), before)

    def test_a_whole_lifecycle_is_audited_with_the_right_people(self):
        sale, entry = self.sale(), self.entry("Survey Plan")
        created = self.call(self.manager, "post", self.list_url(), {"sale": sale.pk, "document_type": entry.pk, "state": "owed"})
        doc = SaleDocument.objects.get(pk=created.data["id"])
        self.call(self.md, "post", self.act("issue", doc), {"issued_on": self.today.isoformat(), "reference": "A"})
        self.call(self.manager, "post", self.act("correct-issuance", doc), {"reason": "Wrong reference", "reference": "B"})
        events = list(sale.events.filter(action__startswith="document").order_by("id"))
        self.assertEqual([(e.action, e.actor) for e in events],
                         [("document_added", self.manager), ("document_issued", self.md),
                          ("document_issuance_corrected", self.manager)])
        self.assertEqual(doc.obligation.events.count(), 3)             # created, closed on issue, correction note
        self.assertNotIn(sale.pk, set(services.exposure_sales(self.a).values_list("pk", flat=True)))


@unittest.skipUnless(_sales_desk_available(), "the sales desk routes are not available here")
class SalesDeskUnchangedTests(SaleDocApiBase):
    def test_the_sale_payload_and_its_can_dictionary_are_exactly_as_before(self):
        sale = self.sale(managed=False)
        self.owed(sale=sale)
        data = self.call(self.manager, "get", reverse("sales-desk-detail", args=[sale.pk])).data
        self.assertNotIn("documents", data)
        self.assertEqual(data["can"], {"record_payment": True, "reverse_payment": True, "edit_buyer": True,
                                       "cancel": True, "reinstate": False})
