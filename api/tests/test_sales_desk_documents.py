"""Phase 8A: POST /api/sales-desk/ with the optional `documents` list.

Only NEW tests live here; every existing test is untouched. The service rules (SLA from the sale date, no
automatic obligations, rollback) are also tested directly in tracker/test_sale_creation_documents.py."""
from datetime import timedelta
from unittest import mock

from api.permissions import MANAGER, MATRIX, MD
from api.tests.test_sales_desk import DeskBase
from tracker import inventory_services
from tracker import document_services as catalogue
from tracker import sale_document_services as docs
from tracker import services
from tracker.models import DocumentType, Obligation, ObligationEvent, ObligationType, Plot, Sale, SaleDocument, SaleEvent

OMITTED = ...                       # "do not send a documents field at all"
INACTIVE = "This document type is inactive, so it cannot be added to a sale."
DUPLICATE = "This document type is already in the list."


class DocumentsApiBase(DeskBase):
    def entry(self, name="Survey Plan", company=None, **extra):
        company = company or self.a
        actor = self.manager if company == self.a else self.b_manager
        return catalogue.create_document_type(company, actor, name=name, **extra)

    def legacy_entry(self, obligation_type):
        entry = DocumentType.objects.create(company=self.a, name=obligation_type.name)
        ObligationType.objects.filter(pk=obligation_type.pk).update(catalogue_entry=entry, is_current=True)
        return entry

    def owed(self, entry):
        return {"document_type": entry.pk, "state": "owed"}

    def issued(self, entry, **extra):
        return {"document_type": entry.pk, "state": "issued",
                "issued_on": (self.today - timedelta(days=5)).isoformat(), **extra}

    def create(self, documents=OMITTED, user=None, **extra):
        payload = self.new_sale_payload(**extra)
        if documents is not OMITTED:
            payload["documents"] = documents
        return self.post(user or self.manager, "sales-desk-list", payload=payload)

    def everything(self):
        return self.counts() + tuple(model.objects.count() for model in (SaleDocument, ObligationEvent))

    def sale_of(self, response):
        return Sale.objects.get(pk=response.data["id"])

    def rows(self, response):
        """What the client really receives: DRF's per-position errors, {"<position>": {field: [messages]}}.
        Only the positions with a problem appear, and JSON turns the positions into text keys."""
        return response.json()["documents"]


class LegacyCreationTests(DocumentsApiBase):
    def test_a_request_without_documents_is_the_legacy_sale_with_the_unchanged_response(self):
        response = self.create()
        self.assertEqual(response.status_code, 201, response.data)
        sale = self.sale_of(response)
        self.assertFalse(sale.documents_managed)
        self.assertEqual([o["type_name"] for o in response.data["obligations"]], ["Allocation Letter"])
        self.assertEqual(set(response.data), set(self.detail(sale)))
        self.assertNotIn("documents", response.data)
        self.assertEqual(SaleDocument.objects.filter(sale=sale).count(), 0)

    def test_an_empty_list_is_the_legacy_sale_including_the_full_payment_trigger(self):
        response = self.create([], amount_paid="1000")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertFalse(self.sale_of(response).documents_managed)
        self.assertEqual({o["type_name"] for o in response.data["obligations"]}, {"Allocation Letter", "Deed"})

    def test_null_is_refused_with_400_and_nothing_is_written(self):
        before = self.everything()
        response = self.create(None)
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("documents", response.data)
        self.assertEqual(self.everything(), before)

    def test_omitted_empty_and_null_need_only_the_add_sale_permission(self):
        narrowed = {"manage_sale_documents": {MD}, "issue_document": {MD}}      # the manager has neither now
        for plot_number in ("N-2", "N-3"):
            inventory_services.register_plot(
                self.a,
                self.manager,
                estate=self.estate,
                plot_number=plot_number,
            )
        with mock.patch.dict(MATRIX, narrowed):
            self.assertEqual(self.create(plot_number="N-1").status_code, 201)
            self.assertEqual(self.create([], plot_number="N-2").status_code, 201)
            self.assertEqual(self.create(None, plot_number="N-3").status_code, 400)       # validation, not 403


class ManagedCreationApiTests(DocumentsApiBase):
    def test_selected_documents_make_a_document_managed_sale_with_the_usual_response(self):
        survey, occupancy = self.entry("Survey Plan"), self.entry("Certificate of Occupancy")
        response = self.create([self.owed(survey), self.issued(occupancy)])
        self.assertEqual(response.status_code, 201, response.data)
        sale = self.sale_of(response)
        self.assertTrue(sale.documents_managed)
        self.assertEqual([o["type_name"] for o in response.data["obligations"]], ["Survey Plan"])
        self.assertEqual(set(response.data), set(self.detail(sale)))                  # no new keys
        self.assertNotIn("documents", response.data)
        self.assertEqual([h["action"] for h in response.data["history"]],
                         ["created", "document_added", "document_added"])

    def test_exactly_the_selected_documents_are_created_and_no_automatic_ones(self):
        allocation, deed = self.legacy_entry(self.alloc), self.legacy_entry(self.deed)
        chosen, unchosen = self.entry("Survey Plan"), self.entry("Receipt")
        response = self.create([self.owed(chosen)], amount_paid="1000")               # fully paid: Deed would be due
        self.assertEqual(response.status_code, 201, response.data)
        sale = self.sale_of(response)
        self.assertEqual(list(sale.documents.values_list("document_type_id", flat=True)), [chosen.pk])
        self.assertFalse(sale.documents.filter(document_type__in=[allocation, deed, unchosen]).exists())
        self.assertEqual(set(sale.obligations.values_list("obligation_type__name", flat=True)), {"Survey Plan"})

    def test_an_owed_document_starts_its_sla_on_the_sale_date(self):
        sale_date = self.today - timedelta(days=100)
        entry = self.entry("Survey Plan", sla_days=30)
        response = self.create([self.owed(entry)], sale_date=sale_date.isoformat())
        self.assertEqual(response.status_code, 201, response.data)
        obligation = response.data["obligations"][0]
        self.assertEqual((obligation["started_on"], obligation["due_date"], obligation["is_open"]),
                         (sale_date.isoformat(), (sale_date + timedelta(days=30)).isoformat(), True))

    def test_a_document_added_later_still_starts_today(self):
        sale = self.sale_of(self.create([self.owed(self.entry("Survey Plan"))],
                                        sale_date=(self.today - timedelta(days=100)).isoformat()))
        later = docs.add_document(sale, self.manager, self.entry("Receipt", sla_days=10), "owed")
        self.assertEqual(later.obligation.started_on, self.today)

    def test_automatic_document_types_recorded_as_issued_create_nothing_outstanding(self):
        allocation, deed = self.legacy_entry(self.alloc), self.legacy_entry(self.deed)
        response = self.create([self.issued(allocation), self.issued(deed)], amount_paid="1000")
        self.assertEqual(response.status_code, 201, response.data)
        sale = self.sale_of(response)
        self.assertEqual(response.data["obligations"], [])
        self.assertEqual(response.data["obligations_open"], 0)
        self.assertEqual(Obligation.objects.filter(sale=sale).count(), 0)
        self.assertNotIn(sale.pk, set(services.exposure_sales(self.a).values_list("pk", flat=True)))

    def test_issuance_metadata_and_the_audit_events_are_recorded(self):
        entry, day = self.entry("Certificate of Occupancy"), self.today - timedelta(days=20)
        response = self.create([{"document_type": entry.pk, "state": "issued", "issued_on": day.isoformat(),
                                "reference": "  AL   001 "}])
        self.assertEqual(response.status_code, 201, response.data)
        sale = self.sale_of(response)
        document = sale.documents.get()
        self.assertEqual((document.state, document.issued_on, document.reference, document.obligation_id),
                         ("issued", day, "AL 001", None))
        self.assertEqual((document.issued_by, document.added_by), (self.manager, self.manager))
        event = SaleEvent.objects.get(sale=sale, action="document_added")
        self.assertEqual(event.actor, self.manager)
        self.assertEqual(event.changes, {"document": [None, "Certificate of Occupancy"], "state": [None, "issued"],
                                         "issued_on": [None, day.isoformat()], "reference": [None, "AL 001"]})

    def test_a_sale_with_only_issued_documents_is_still_document_managed(self):
        response = self.create([self.issued(self.entry("Survey Plan"))])
        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(self.sale_of(response).documents_managed)

    def test_the_client_cannot_choose_obligations_or_companies(self):
        entry = self.entry("Survey Plan")
        stray = Obligation.objects.create(
            company=self.a, sale=self.s2, obligation_type=catalogue.current_work_type(entry),
            started_on=self.today, due_date=self.today)
        item = {**self.owed(entry), "obligation": stray.pk, "company": self.b.pk, "sale": self.s1.pk}
        response = self.create([item], company=self.b.pk)
        self.assertEqual(response.status_code, 201, response.data)
        document = self.sale_of(response).documents.get()
        self.assertEqual(document.company_id, self.a.pk)
        self.assertNotEqual(document.obligation_id, stray.pk)
        self.assertFalse(SaleDocument.objects.filter(obligation=stray).exists())
        self.assertEqual(self.sale_of(response).company_id, self.a.pk)


class ValidationApiTests(DocumentsApiBase):
    def setUp(self):
        super().setUp()
        self.a_entry, self.b_entry, self.c_entry = (
            self.entry("Survey Plan"), self.entry("Certificate of Occupancy"), self.entry("Receipt"))

    def refused(self, documents, status=400, **extra):
        before = self.everything()
        response = self.create(documents, **extra)
        self.assertEqual(response.status_code, status, response.data)
        self.assertEqual(self.everything(), before)                      # nothing was written, whatever the refusal
        return response

    def test_foreign_and_unknown_document_types_are_400_at_their_own_position(self):
        foreign = self.entry("Foreign Doc", company=self.b)
        response = self.refused([self.owed(self.a_entry), {"document_type": foreign.pk, "state": "owed"},
                                 {"document_type": 987654, "state": "owed"}])
        rows = self.rows(response)
        self.assertEqual(set(rows), {"1", "2"})                          # position 0 is fine, so it is absent
        self.assertEqual((set(rows["1"]), set(rows["2"])), ({"document_type"}, {"document_type"}))

    def test_an_inactive_document_type_is_409_at_its_own_position(self):
        catalogue.set_document_type_active(self.c_entry, self.manager, False)
        response = self.refused([self.owed(self.a_entry), self.owed(self.c_entry), self.owed(self.b_entry)], 409)
        self.assertEqual(response.json(), {"documents": {"1": {"document_type": [INACTIVE]}}})

    def test_a_duplicate_document_type_is_refused_at_its_later_position(self):
        response = self.refused([self.owed(self.a_entry), self.owed(self.b_entry), self.issued(self.a_entry)])
        self.assertEqual(self.rows(response), {"2": {"document_type": [DUPLICATE]}})

    def test_only_owed_and_issued_are_accepted(self):
        for state in ("not_applicable", "bogus", ""):
            response = self.refused([{"document_type": self.a_entry.pk, "state": state}])
            self.assertIn("state", self.rows(response)["0"], state)
        response = self.refused([{"document_type": self.a_entry.pk}])
        self.assertIn("state", self.rows(response)["0"])
        response = self.refused([{"state": "owed"}])
        self.assertIn("document_type", self.rows(response)["0"])

    def test_an_issued_document_needs_a_valid_issue_date(self):
        future = (self.today + timedelta(days=9)).isoformat()
        for extra in ({}, {"issued_on": None}, {"issued_on": future}, {"issued_on": "1980-01-01"},
                      {"issued_on": "31/02/2026"}, {"issued_on": "soon"}):
            response = self.refused([{"document_type": self.a_entry.pk, "state": "issued", **extra}])
            self.assertIn("issued_on", self.rows(response)["0"], extra)

    def test_an_owed_document_refuses_an_issue_date_and_a_reference(self):
        for extra in ({"issued_on": self.today.isoformat()}, {"reference": "REF-1"}):
            response = self.refused([{**self.owed(self.a_entry), **extra}])
            self.assertEqual(self.rows(response)["0"],
                             {"issued_on": ["An issue date and reference can only be given for an issued document."]})

    def test_a_reference_that_is_too_long_is_refused(self):
        response = self.refused([self.issued(self.a_entry, reference="r" * 121)])
        self.assertIn("reference", self.rows(response)["0"])

    def test_every_problem_is_reported_at_its_own_position_in_one_answer(self):
        response = self.refused([
            self.owed(self.a_entry),                                          # 0: fine
            {"document_type": 987654, "state": "owed"},                       # 1: unknown type
            {"document_type": self.b_entry.pk, "state": "issued"},            # 2: no issue date
            {"document_type": self.c_entry.pk, "state": "bogus"},             # 3: invalid state
            self.owed(self.a_entry),                                          # 4: the same type as position 0
        ])
        rows = self.rows(response)
        self.assertEqual(set(rows), {"1", "2", "3", "4"})                # position 0 is fine, so it is absent
        self.assertEqual(set(rows["1"]), {"document_type"})
        self.assertEqual(rows["2"], {"issued_on": ["The issue date is required."]})
        self.assertEqual(set(rows["3"]), {"state"})
        self.assertEqual(rows["4"], {"document_type": [DUPLICATE]})

    def test_a_list_of_the_wrong_shape_or_size_is_refused(self):
        for documents in ("abc", {}, [1], ["abc"], [self.owed(self.a_entry)] * 51):
            response = self.refused(documents)
            self.assertIn("documents", response.data, documents)
        self.assertIn("non_field_errors", self.rows(self.refused([1]))["0"])

    def test_sale_level_problems_are_answered_exactly_as_before(self):
        response = self.refused([self.owed(self.a_entry)], price="0")
        self.assertIn("detail", response.data)

        plot_number = "OCC-1"
        inventory_services.register_plot(
            self.a,
            self.manager,
            estate=self.estate,
            plot_number=plot_number,
        )
        plot = Plot.objects.get(estate=self.estate, plot_number=plot_number)
        existing_sale = services.create_sale(
            self.a, plot, self.s2.buyer, 1000, self.today, 400
        )

        payload = dict(
            estate=self.estate.pk,
            estate_name="",
            plot_number=plot_number,
            buyer_name="Someone Else",
        )
        response = self.refused([self.owed(self.a_entry)], 409, **payload)
        self.assertEqual(
            (response.data["existing_sale"]["id"], response.data["existing_sale"]["buyer_name"]),
            (existing_sale.pk, "Bayo Lawal"),
        )


class PermissionApiTests(DocumentsApiBase):
    def setUp(self):
        super().setUp()
        self.entry_a, self.entry_b = self.entry("Survey Plan"), self.entry("Certificate of Occupancy")

    def test_the_role_matrix_is_unchanged(self):
        for action in ("add_sale", "manage_sale_documents", "issue_document"):
            self.assertEqual(MATRIX[action], {MD, MANAGER}, action)

    def test_other_roles_and_outsiders_are_refused_and_nothing_is_written(self):
        before = self.everything()
        for user in (self.staff_legal, self.sales, self.viewer, self.outsider):
            response = self.create([self.owed(self.entry_a)], user=user)
            self.assertEqual(response.status_code, 403, user.role)
        self.assertEqual(self.everything(), before)

    def test_an_owed_selection_needs_manage_sale_documents_as_well_as_add_sale(self):
        before = self.everything()
        with mock.patch.dict(MATRIX, {"manage_sale_documents": {MD}}):
            self.assertEqual(self.create([self.owed(self.entry_a)], user=self.manager).status_code, 403)
            self.assertEqual(self.everything(), before)
            self.assertEqual(self.create([self.owed(self.entry_a)], user=self.md).status_code, 201)

    def test_an_issued_selection_needs_issue_document_as_well(self):
        for plot_number in ("N-2", "N-3"):
            inventory_services.register_plot(
                self.a,
                self.manager,
                estate=self.estate,
                plot_number=plot_number,
            )
        with mock.patch.dict(MATRIX, {"issue_document": {MD}}):
            before = self.everything()
            self.assertEqual(self.create([self.issued(self.entry_a)], user=self.manager).status_code, 403)
            mixed = [self.owed(self.entry_b), self.issued(self.entry_a)]
            self.assertEqual(self.create(mixed, user=self.manager).status_code, 403)
            self.assertEqual(self.everything(), before)
            owed_only = self.create([self.owed(self.entry_a)], plot_number="N-2", user=self.manager)
            self.assertEqual(owed_only.status_code, 201)
            self.assertEqual(self.create([self.issued(self.entry_b)], plot_number="N-3", user=self.md).status_code, 201)

    def test_the_permission_answer_comes_before_any_validation(self):
        with mock.patch.dict(MATRIX, {"manage_sale_documents": {MD}}):
            response = self.create([{"document_type": 987654, "state": "bogus"}], user=self.manager)
        self.assertEqual(response.status_code, 403)


class AtomicityApiTests(DocumentsApiBase):
    def test_an_invalid_second_document_saves_neither_the_sale_nor_the_first_document(self):
        good, bad = self.entry("Survey Plan"), self.entry("Receipt")
        catalogue.set_document_type_active(bad, self.manager, False)
        before = self.everything()
        response = self.create([self.owed(good), self.owed(bad)])
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.json(), {"documents": {"1": {"document_type": [INACTIVE]}}})
        self.assertEqual(self.everything(), before)

    def test_a_crash_while_creating_the_second_document_rolls_the_whole_request_back(self):
        a, b = self.entry("Survey Plan"), self.entry("Certificate of Occupancy")
        before = self.everything()
        real, calls = docs._log_sale, []

        def second_call_fails(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError("boom")
            return real(*args, **kwargs)

        with mock.patch("tracker.sale_document_services._log_sale", side_effect=second_call_fails):
            with self.assertRaises(RuntimeError):
                self.create([self.owed(a), self.issued(b)])
        self.assertEqual(self.everything(), before)

    def test_a_failure_recording_an_issuance_audit_rolls_the_whole_request_back(self):
        entry = self.entry("Certificate of Occupancy")
        before = self.everything()
        with mock.patch("tracker.sale_document_services._log_sale", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.create([self.issued(entry, reference="AL-001")])
        self.assertEqual(self.everything(), before)
