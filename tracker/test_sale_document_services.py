from datetime import timedelta
from unittest import mock

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError
from django.utils import timezone

from importer.commit import undo_blockers
from . import document_services as catalogue
from . import reports, services
from . import sale_document_services as docs
from .models import (
    Buyer, Company, DocumentType, Estate, ImportBatch, Obligation, ObligationEvent, ObligationType, Plot, Sale,
    SaleDocument, SaleEvent, User,
)
from .test_cancel_and_reports import Base

OWED, ISSUED, NA = SaleDocument.State.OWED, SaleDocument.State.ISSUED, SaleDocument.State.NOT_APPLICABLE


class DocBase(Base):
    def setUp(self):
        super().setUp()
        self.md = User.objects.create_user("md", password="x", company=self.co, role="md")

    # ---- fixtures ----

    def entry(self, name="Survey Plan", **extra):
        return catalogue.create_document_type(self.co, self.manager, name=name, **extra)

    def legacy_entry(self, obligation_type):
        """What the 1B backfill and 0011 leave behind: an entry paired with an existing automatic type."""
        entry = DocumentType.objects.create(company=self.co, name=obligation_type.name)
        ObligationType.objects.filter(pk=obligation_type.pk).update(catalogue_entry=entry, is_current=True)
        return entry

    def managed_sale(self, paid=400):
        """A document-managed sale: no automatic obligations, exactly as Phase 2A will create one."""
        sale = self.sale(price=1000, paid=paid)
        sale.obligations.all().delete()
        Sale.objects.filter(pk=sale.pk).update(documents_managed=True)
        return Sale.objects.get(pk=sale.pk)

    def other_sale(self):
        estate = Estate.objects.create(company=self.other, name="Elsewhere")
        plot = Plot.objects.create(company=self.other, estate=estate, plot_number="X-1")
        buyer = Buyer.objects.create(company=self.other, full_name="Other Buyer")
        return services.create_sale(self.other, plot, buyer, 500, self.today, 500, self.today)

    # ---- actions ----

    def add(self, sale, entry, state="owed", actor=None, **extra):
        return docs.add_document(sale, actor or self.manager, entry, state, **extra)

    def owed(self, sale=None, entry=None):
        return self.add(sale or self.managed_sale(), entry or self.entry())

    def issued(self, **extra):
        doc = self.owed()
        return docs.issue_document(doc, self.manager, extra.pop("issued_on", self.today), **extra)

    def complete(self, obligation):
        return services.update_obligation(obligation, self.manager, status="completed")

    # ---- observations ----

    def counts(self):
        return (SaleDocument.objects.count(), Obligation.objects.count(), SaleEvent.objects.count(),
                ObligationEvent.objects.count())

    def event(self, sale, action):
        return sale.events.filter(action=action).latest("id")

    def exposed(self):
        return set(services.exposure_sales(self.co).values_list("pk", flat=True))

    def used_work_type(self, entry):
        """Give the entry's current work type an obligation on some sale, so a rename must create a new type."""
        sale = self.sale(price=1000, paid=400)
        work = catalogue.current_work_type(entry)
        Obligation.objects.create(company=self.co, sale=sale, obligation_type=work, started_on=self.today,
                                  due_date=self.today)
        return work


class AddOwedTests(DocBase):
    def test_a_managed_sale_gets_an_obligation_under_the_current_work_type(self):
        sale = self.managed_sale()
        entry = self.entry(sla_days=45, default_department=self.legal)
        doc = self.add(sale, entry)
        obligation, work = doc.obligation, catalogue.current_work_type(entry)
        self.assertEqual((doc.state, doc.name_snapshot, doc.added_by, doc.company_id, doc.sale_id),
                         (OWED, "Survey Plan", self.manager, self.co.pk, sale.pk))
        self.assertEqual((obligation.obligation_type, obligation.sale_id, obligation.company_id, obligation.status),
                         (work, sale.pk, self.co.pk, "not_started"))
        self.assertEqual((obligation.started_on, obligation.due_date, obligation.department_id),
                         (self.today, self.today + timedelta(days=45), self.legal.pk))
        self.assertEqual(sale.obligations.count(), 1)
        created = obligation.events.get()
        self.assertIn("Created for the sale's document", created.note)
        self.assertEqual(created.actor, self.manager)
        event = self.event(sale, "document_added")
        self.assertEqual(event.actor, self.manager)
        self.assertEqual(event.changes, {"document": [None, "Survey Plan"], "state": [None, "owed"],
                                         "obligation": [None, obligation.pk]})
        sale.refresh_from_db()
        self.assertTrue(sale.documents_managed)

    def test_a_legacy_sale_stays_legacy_and_keeps_its_own_obligations(self):
        sale = self.sale(price=1000, paid=400)                           # legacy: has its Allocation Letter
        self.add(sale, self.entry())
        sale.refresh_from_db()
        self.assertFalse(sale.documents_managed)
        self.assertEqual(set(sale.obligations.values_list("obligation_type__name", flat=True)),
                         {"Allocation Letter", "Survey Plan"})

    def test_an_existing_legacy_obligation_is_adopted_not_duplicated(self):
        entry = self.legacy_entry(self.deed)
        sale = self.sale(price=1000, paid=1000)                          # Allocation Letter and Deed
        existing = sale.obligations.get(obligation_type=self.deed)
        before = (existing.status, existing.started_on, existing.due_date, existing.owner_id)
        obligations = Obligation.objects.count()
        doc = self.add(sale, entry)
        self.assertEqual(doc.obligation_id, existing.pk)
        self.assertEqual(Obligation.objects.count(), obligations)
        existing.refresh_from_db()
        self.assertEqual((existing.status, existing.started_on, existing.due_date, existing.owner_id), before)
        self.assertIn("adopted", existing.events.latest("id").note)
        event = self.event(sale, "document_added")
        self.assertIn("adopted", event.note)
        self.assertEqual(event.changes["obligation"], [None, existing.pk])

    def test_an_obligation_created_under_an_earlier_work_type_is_adopted(self):
        entry = self.entry("Survey Plan")
        old = self.used_work_type(entry)
        catalogue.rename_document_type(entry, self.manager, "Land Survey")
        new = catalogue.current_work_type(entry)
        self.assertNotEqual(old.pk, new.pk)
        sale = self.sale(price=1000, paid=400)
        legacy = Obligation.objects.create(company=self.co, sale=sale, obligation_type=old, started_on=self.today,
                                           due_date=self.today)
        doc = self.add(sale, entry)
        self.assertEqual(doc.obligation_id, legacy.pk)
        self.assertEqual(doc.name_snapshot, "Land Survey")
        self.assertFalse(Obligation.objects.filter(sale=sale, obligation_type=new).exists())

    def test_only_an_open_existing_obligation_is_adopted_and_its_status_is_not_changed(self):
        for status in ("not_started", "in_progress", "awaiting_external"):
            sale = self.sale(price=1000, paid=400)                       # first, so it generates nothing for the type below
            entry = self.legacy_entry(ObligationType.objects.create(
                company=self.co, name=f"Legacy {status}", trigger="on_sale", sla_days=30))
            existing = Obligation.objects.create(
                company=self.co, sale=sale, obligation_type=entry.obligation_types.get(), status=status,
                started_on=self.today, due_date=self.today)
            doc = self.add(sale, entry)
            existing.refresh_from_db()
            self.assertEqual((doc.obligation_id, existing.status), (existing.pk, status), status)

    def test_an_existing_completed_or_not_applicable_obligation_is_refused_and_nothing_changes(self):
        cases = (("completed", "already completed"), ("not_applicable", "marked not applicable"))
        for status, wording in cases:
            sale = self.sale(price=1000, paid=400)                       # first, so it generates nothing for the type below
            entry = self.legacy_entry(ObligationType.objects.create(
                company=self.co, name=f"Legacy {status}", trigger="on_sale", sla_days=30))
            existing = Obligation.objects.create(
                company=self.co, sale=sale, obligation_type=entry.obligation_types.get(), status=status,
                started_on=self.today, due_date=self.today, completed_on=self.today if status == "completed" else None)
            ObligationEvent.objects.create(company=self.co, obligation=existing, note="History before")
            fields = ("status", "completed_on", "started_on", "due_date", "owner_id", "last_updated_at")
            before_obligation = Obligation.objects.filter(pk=existing.pk).values(*fields).get()
            before = self.counts()
            before_flag = Sale.objects.get(pk=sale.pk).documents_managed
            with self.assertRaises(docs.SaleDocumentStateError, msg=status) as caught:
                self.add(sale, entry)
            message = str(caught.exception)
            self.assertIn(wording, message, status)
            self.assertIn("Review", message, status)
            self.assertIn("issued or delivered", message, status)
            self.assertEqual(caught.exception.field, "document_type")
            self.assertEqual(self.counts(), before, status)                          # no document, obligation or event
            self.assertEqual(Obligation.objects.filter(pk=existing.pk).values(*fields).get(), before_obligation)
            self.assertFalse(SaleDocument.objects.filter(sale=sale).exists())
            self.assertEqual(Sale.objects.get(pk=sale.pk).documents_managed, before_flag)

    def test_a_refused_adoption_does_not_increase_exposure(self):
        entry = self.legacy_entry(self.deed)
        sale = self.sale(price=1000, paid=1000)
        for obligation in sale.obligations.all():
            self.complete(obligation)                                    # all legacy work finished: not in exposure
        self.assertNotIn(sale.pk, self.exposed())
        with self.assertRaises(docs.SaleDocumentStateError):
            self.add(sale, entry)
        self.assertNotIn(sale.pk, self.exposed())

    def test_a_managed_sale_that_already_has_an_obligation_for_the_entry_is_refused(self):
        sale, entry = self.managed_sale(), self.entry()
        Obligation.objects.create(company=self.co, sale=sale, obligation_type=catalogue.current_work_type(entry),
                                  started_on=self.today, due_date=self.today)
        before = self.counts()
        with self.assertRaises(docs.SaleDocumentStateError):
            self.add(sale, entry)
        self.assertEqual(self.counts(), before)

    def test_two_obligations_for_one_entry_are_refused_as_ambiguous(self):
        entry = self.entry("Survey Plan")
        old = self.used_work_type(entry)
        catalogue.rename_document_type(entry, self.manager, "Land Survey")
        sale = self.sale(price=1000, paid=400)
        for work in (old, catalogue.current_work_type(entry)):
            Obligation.objects.create(company=self.co, sale=sale, obligation_type=work, started_on=self.today,
                                      due_date=self.today)
        before = self.counts()
        with self.assertRaises(docs.SaleDocumentStateError):
            self.add(sale, entry)
        self.assertEqual(self.counts(), before)

    def test_an_entry_without_a_current_work_type_cannot_be_added_as_owed(self):
        entry = self.entry()
        ObligationType.objects.filter(catalogue_entry=entry).update(is_current=False)
        sale = self.managed_sale()
        before = self.counts()
        with self.assertRaises(docs.SaleDocumentStateError):
            self.add(sale, entry)
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.add(sale, entry, "issued", issued_on=self.today).state, ISSUED)   # issued needs none

    def test_an_issue_date_or_reference_is_refused_for_an_owed_document(self):
        sale, entry = self.managed_sale(), self.entry()
        for extra in (dict(issued_on=self.today), dict(reference="REF-1")):
            with self.assertRaises(docs.SaleDocumentError, msg=extra):
                self.add(sale, entry, **extra)
        self.assertEqual(SaleDocument.objects.count(), 0)

    def test_the_same_document_cannot_be_added_twice(self):
        sale, entry = self.managed_sale(), self.entry()
        self.add(sale, entry)
        before = self.counts()
        with self.assertRaises(docs.SaleDocumentStateError):
            self.add(sale, entry)
        with self.assertRaises(docs.SaleDocumentStateError):
            self.add(sale, entry, "issued", issued_on=self.today)
        self.assertEqual(self.counts(), before)

    def test_a_lost_race_is_reported_as_a_duplicate_and_leaves_nothing_behind(self):
        sale, entry = self.managed_sale(), self.entry()
        before = self.counts()
        with mock.patch.object(SaleDocument.objects, "create", side_effect=IntegrityError("duplicate")):
            with self.assertRaises(docs.SaleDocumentStateError):
                self.add(sale, entry)
        self.assertEqual(self.counts(), before)                          # the obligation was rolled back too


class AddIssuedTests(DocBase):
    def test_creates_an_issued_document_without_an_obligation(self):
        sale, entry = self.managed_sale(), self.entry()
        obligations = Obligation.objects.count()
        doc = self.add(sale, entry, "issued", issued_on=self.today - timedelta(days=30), reference="  REF   1 ")
        self.assertEqual((doc.state, doc.reference, doc.obligation, doc.issued_by, doc.added_by),
                         (ISSUED, "REF 1", None, self.manager, self.manager))
        self.assertEqual(doc.issued_on, self.today - timedelta(days=30))
        self.assertIsNotNone(doc.issued_at)
        self.assertEqual(Obligation.objects.count(), obligations)
        event = self.event(sale, "document_added")
        self.assertEqual(event.changes, {"document": [None, "Survey Plan"], "state": [None, "issued"],
                                         "issued_on": [None, doc.issued_on.isoformat()],
                                         "reference": [None, "REF 1"]})

    def test_accepts_a_date_object_or_an_iso_string(self):
        sale = self.managed_sale()
        a = self.add(sale, self.entry("A"), "issued", issued_on=self.today)
        b = self.add(sale, self.entry("B"), "issued", issued_on=self.today.isoformat())
        self.assertEqual((a.issued_on, b.issued_on), (self.today, self.today))

    def test_the_date_is_required_and_checked(self):
        sale, entry = self.managed_sale(), self.entry()
        for bad in (None, "", self.today + timedelta(days=9), "1980-01-01", "31/02/2026", "soon"):
            with self.assertRaises(docs.SaleDocumentError, msg=bad) as caught:
                self.add(sale, entry, "issued", issued_on=bad)
            self.assertEqual(caught.exception.field, "issued_on", bad)
        with self.assertRaises(docs.SaleDocumentError) as caught:
            self.add(sale, entry, "issued", issued_on=self.today, reference="r" * 121)
        self.assertEqual(caught.exception.field, "reference")
        self.assertEqual(SaleDocument.objects.count(), 0)

    def test_it_is_refused_when_the_sale_already_has_an_obligation_for_the_entry(self):
        entry = self.legacy_entry(self.deed)
        sale = self.sale(price=1000, paid=1000)                          # the Deed obligation exists
        before = self.counts()
        with self.assertRaises(docs.SaleDocumentStateError) as caught:
            self.add(sale, entry, "issued", issued_on=self.today)
        self.assertIn("owed first", str(caught.exception))
        self.assertEqual(self.counts(), before)

    def test_an_issued_document_stops_the_legacy_obligation_being_created_later(self):
        entry = self.legacy_entry(self.deed)
        sale = self.sale(price=1000, paid=400)
        self.add(sale, entry, "issued", issued_on=self.today)
        result = services.record_payment(sale, self.manager, "600", idempotency_key="k1")
        self.assertEqual(result.obligations_created, [])
        self.assertFalse(sale.obligations.filter(obligation_type=self.deed).exists())


class AddRefusalTests(DocBase):
    def test_refusals_write_nothing(self):
        sale, entry = self.managed_sale(), self.entry()
        inactive = self.entry("Inactive Doc")
        catalogue.set_document_type_active(inactive, self.manager, False)
        foreign_entry = catalogue.create_document_type(self.other, self.eve, name="Foreign Doc")
        cancelled = self.managed_sale()
        services.cancel_sale(cancelled, self.manager, "defaulted")
        foreign_sale = self.other_sale()
        before = self.counts()
        cases = [
            (docs.SaleDocumentStateError, lambda: self.add(sale, inactive)),
            (docs.SaleDocumentStateError, lambda: self.add(cancelled, entry)),
            (PermissionDenied, lambda: self.add(sale, entry, actor=self.eve)),
            (PermissionDenied, lambda: self.add(sale, foreign_entry)),
            (PermissionDenied, lambda: self.add(foreign_sale, entry, actor=self.eve)),
            (docs.SaleDocumentError, lambda: self.add(sale, entry, state="not_applicable")),
            (docs.SaleDocumentError, lambda: self.add(sale, entry, state="bogus")),
        ]
        for error, call in cases:
            with self.assertRaises(error):
                call()
        self.assertEqual(self.counts(), before)
        self.assertEqual(SaleDocument.objects.count(), 0)


class IssueTests(DocBase):
    def test_issuing_closes_an_open_obligation_and_records_everything(self):
        doc = self.owed()
        sale, obligation = doc.sale, doc.obligation
        when = self.today - timedelta(days=10)
        docs.issue_document(doc, self.md, when, reference=" TRF  1 ")
        doc.refresh_from_db()
        obligation.refresh_from_db()
        self.assertEqual((doc.state, doc.issued_on, doc.reference, doc.issued_by), (ISSUED, when, "TRF 1", self.md))
        self.assertIsNotNone(doc.issued_at)
        self.assertEqual((obligation.status, obligation.completed_on), ("completed", self.today))   # the real date is on the document
        last = obligation.events.latest("id")
        self.assertEqual(last.changes["status"], ["not_started", "completed"])
        self.assertIn("Document issued on", last.note)
        self.assertEqual(last.actor, self.md)
        event = self.event(sale, "document_issued")
        self.assertEqual(event.actor, self.md)
        self.assertEqual(event.changes, {"state": ["owed", "issued"], "issued_on": [None, when.isoformat()],
                                         "reference": ["", "TRF 1"]})

    def test_an_already_completed_obligation_is_left_alone_but_the_issuance_is_still_recorded(self):
        doc = self.owed()
        self.complete(doc.obligation)
        doc.refresh_from_db()
        self.assertEqual(doc.state, OWED)                                 # preparing it did not issue it
        completed_on = doc.obligation.completed_on
        docs.issue_document(doc, self.manager, self.today)
        obligation = Obligation.objects.get(pk=doc.obligation_id)
        self.assertEqual((obligation.status, obligation.completed_on), ("completed", completed_on))
        last = obligation.events.latest("id")
        self.assertEqual(last.changes, {})
        self.assertIn("Document issued on", last.note)
        self.assertEqual(self.event(doc.sale, "document_issued").changes["state"], ["owed", "issued"])

    def test_completing_the_obligation_alone_never_issues_the_document(self):
        doc = self.owed()
        self.complete(doc.obligation)
        doc.refresh_from_db()
        self.assertEqual((doc.state, doc.issued_on, doc.issued_by), (OWED, None, None))
        self.assertFalse(doc.sale.events.filter(action="document_issued").exists())

    def test_the_date_and_reference_are_checked_and_a_refusal_changes_nothing(self):
        doc = self.owed()
        before = self.counts()
        for bad in (None, "", self.today + timedelta(days=9), "1980-01-01", "31/02/2026"):
            with self.assertRaises(docs.SaleDocumentError, msg=bad) as caught:
                docs.issue_document(doc, self.manager, bad)
            self.assertEqual(caught.exception.field, "issued_on")
        with self.assertRaises(docs.SaleDocumentError):
            docs.issue_document(doc, self.manager, self.today, reference="r" * 121)
        doc.refresh_from_db()
        self.assertEqual((doc.state, self.counts()), (OWED, before))

    def test_wrong_states_are_refused_with_a_state_error(self):
        issued = self.issued()
        skipped = self.owed(sale=self.managed_sale(), entry=self.entry("Other Doc"))
        docs.mark_not_applicable(skipped, self.manager, "Not needed")
        cancelled_doc = self.owed(sale=self.managed_sale(), entry=self.entry("Third Doc"))
        services.cancel_sale(cancelled_doc.sale, self.manager, "defaulted")
        for doc in (issued, skipped, cancelled_doc):
            with self.assertRaises(docs.SaleDocumentStateError):
                docs.issue_document(doc, self.manager, self.today)
        issued.refresh_from_db()
        self.assertEqual(issued.issued_on, self.today)

    def test_another_companys_actor_is_refused(self):
        doc = self.owed()
        with self.assertRaises(PermissionDenied):
            docs.issue_document(doc, self.eve, self.today)
        doc.refresh_from_db()
        self.assertEqual(doc.state, OWED)

    def test_an_obligation_marked_not_applicable_must_be_reopened_first(self):
        doc = self.owed()
        Obligation.objects.filter(pk=doc.obligation_id).update(status="not_applicable")      # bypasses the guard
        with self.assertRaises(docs.SaleDocumentStateError):
            docs.issue_document(doc, self.manager, self.today)
        doc.refresh_from_db()
        self.assertEqual(doc.state, OWED)

    def test_the_sale_leaves_exposure_when_its_last_owed_document_is_issued(self):
        doc = self.owed()
        self.assertIn(doc.sale_id, self.exposed())
        docs.issue_document(doc, self.manager, self.today)
        self.assertNotIn(doc.sale_id, self.exposed())


class MarkNotApplicableTests(DocBase):
    def test_a_reason_of_at_least_three_characters_is_required(self):
        doc = self.owed()
        for bad in (None, "", "   ", "ab", " ab "):
            with self.assertRaises(docs.SaleDocumentError, msg=bad) as caught:
                docs.mark_not_applicable(doc, self.manager, bad)
            self.assertEqual(caught.exception.field, "reason")
        doc.refresh_from_db()
        self.assertEqual(doc.state, OWED)
        docs.mark_not_applicable(doc, self.manager, "abc")

    def test_an_open_obligation_is_closed_and_everything_is_recorded(self):
        doc = self.owed()
        sale, obligation = doc.sale, doc.obligation
        docs.mark_not_applicable(doc, self.md, "  Buyer already holds it ")
        doc.refresh_from_db()
        obligation.refresh_from_db()
        self.assertEqual((doc.state, doc.not_applicable_reason, doc.not_applicable_by),
                         (NA, "Buyer already holds it", self.md))
        self.assertIsNotNone(doc.not_applicable_at)
        self.assertEqual(obligation.status, "not_applicable")
        last = obligation.events.latest("id")
        self.assertEqual(last.changes["status"], ["not_started", "not_applicable"])
        self.assertIn("Buyer already holds it", last.note)
        event = self.event(sale, "document_not_applicable")
        self.assertEqual((event.actor, event.changes), (self.md, {"state": ["owed", "not_applicable"]}))
        self.assertIn("Buyer already holds it", event.note)
        self.assertNotIn(sale.pk, self.exposed())

    def test_a_completed_obligation_is_left_completed(self):
        doc = self.owed()
        self.complete(doc.obligation)
        docs.mark_not_applicable(doc, self.manager, "Not required")
        obligation = Obligation.objects.get(pk=doc.obligation_id)
        self.assertEqual(obligation.status, "completed")
        self.assertEqual(obligation.events.latest("id").changes, {})
        doc.refresh_from_db()
        self.assertEqual(doc.state, NA)
        self.assertNotIn(doc.sale_id, self.exposed())

    def test_wrong_states_and_other_companies_are_refused(self):
        issued = self.issued()
        skipped = self.owed(sale=self.managed_sale(), entry=self.entry("Other Doc"))
        docs.mark_not_applicable(skipped, self.manager, "Not needed")
        cancelled_doc = self.owed(sale=self.managed_sale(), entry=self.entry("Third Doc"))
        services.cancel_sale(cancelled_doc.sale, self.manager, "defaulted")
        for doc in (issued, skipped, cancelled_doc):
            with self.assertRaises(docs.SaleDocumentStateError):
                docs.mark_not_applicable(doc, self.manager, "Not needed")
        open_doc = self.owed(sale=self.managed_sale(), entry=self.entry("Fourth Doc"))
        with self.assertRaises(PermissionDenied):
            docs.mark_not_applicable(open_doc, self.eve, "Not needed")
        issued.refresh_from_db()
        self.assertEqual(issued.state, ISSUED)


class CorrectIssuanceTests(DocBase):
    def setUp(self):
        super().setUp()
        self.original_day = self.today - timedelta(days=20)
        self.doc = self.owed()
        docs.issue_document(self.doc, self.manager, self.original_day, reference="A-1")
        self.doc.refresh_from_db()

    def correct(self, **extra):
        extra.setdefault("reason", "Typed the wrong date")
        return docs.correct_issuance(self.doc, extra.pop("actor", self.md), extra.pop("reason"), **extra)

    def test_the_reason_is_required(self):
        for bad in (None, "", "ab", "  "):
            with self.assertRaises(docs.SaleDocumentError, msg=bad) as caught:
                self.correct(reason=bad, reference="B-2")
            self.assertEqual(caught.exception.field, "reason")

    def test_a_correction_needs_a_real_change(self):
        before = self.counts()
        for extra in ({}, {"reference": "A-1"}, {"issued_on": self.original_day},
                      {"issued_on": self.original_day.isoformat(), "reference": "A-1"}):
            with self.assertRaises(docs.SaleDocumentError, msg=extra) as caught:
                self.correct(**extra)
            self.assertIn("Nothing to change", str(caught.exception))
        self.assertEqual(self.counts(), before)

    def test_before_and_after_are_recorded_and_the_original_issuer_is_kept(self):
        issued_at, issued_by = self.doc.issued_at, self.doc.issued_by
        new_day = self.today - timedelta(days=15)
        self.correct(issued_on=new_day, reference="B-2")
        self.doc.refresh_from_db()
        self.assertEqual((self.doc.issued_on, self.doc.reference), (new_day, "B-2"))
        self.assertEqual((self.doc.issued_by, self.doc.issued_at, self.doc.state), (issued_by, issued_at, ISSUED))
        event = self.event(self.doc.sale, "document_issuance_corrected")
        self.assertEqual(event.actor, self.md)                                       # who corrected it
        self.assertEqual(event.changes, {"issued_on": [self.original_day.isoformat(), new_day.isoformat()],
                                         "reference": ["A-1", "B-2"]})
        self.assertIn("Typed the wrong date", event.note)
        self.assertIn("Issuance corrected", self.doc.obligation.events.latest("id").note)

    def test_only_the_changed_fields_are_recorded(self):
        self.correct(reference="B-2", issued_on=self.original_day)
        self.assertEqual(self.event(self.doc.sale, "document_issuance_corrected").changes,
                         {"reference": ["A-1", "B-2"]})
        self.correct(issued_on=self.today - timedelta(days=5))
        self.assertEqual(list(self.event(self.doc.sale, "document_issuance_corrected").changes), ["issued_on"])

    def test_a_reference_can_be_cleared(self):
        self.correct(reference="")
        self.doc.refresh_from_db()
        self.assertEqual(self.doc.reference, "")
        self.assertEqual(self.event(self.doc.sale, "document_issuance_corrected").changes,
                         {"reference": ["A-1", ""]})

    def test_a_document_added_as_issued_can_be_corrected_too(self):
        doc = self.add(self.managed_sale(), self.entry("Other Doc"), "issued", issued_on=self.today)
        docs.correct_issuance(doc, self.manager, "Wrong day", issued_on=self.original_day)
        doc.refresh_from_db()
        self.assertEqual(doc.issued_on, self.original_day)

    def test_bad_dates_wrong_states_and_other_companies_are_refused(self):
        for bad in (self.today + timedelta(days=9), "1980-01-01", "31/02/2026"):
            with self.assertRaises(docs.SaleDocumentError, msg=bad) as caught:
                self.correct(issued_on=bad)
            self.assertEqual(caught.exception.field, "issued_on")
        with self.assertRaises(docs.SaleDocumentError):
            self.correct(reference="r" * 121)
        owed = self.owed(sale=self.managed_sale(), entry=self.entry("Other Doc"))
        with self.assertRaises(docs.SaleDocumentStateError):
            docs.correct_issuance(owed, self.manager, "Not issued yet", reference="X")
        with self.assertRaises(PermissionDenied):
            self.correct(reference="B-2", actor=self.eve)
        services.cancel_sale(self.doc.sale, self.manager, "defaulted")
        with self.assertRaises(docs.SaleDocumentStateError):
            self.correct(reference="B-2")
        self.doc.refresh_from_db()
        self.assertEqual((self.doc.issued_on, self.doc.reference), (self.original_day, "A-1"))


class RelationshipTests(DocBase):
    """The database does not stop a document being linked to the wrong obligation, so the services do."""

    def stray(self, sale, entry):
        """An obligation of the entry's work type that no document owns yet."""
        return Obligation.objects.create(
            company=sale.company, sale=sale, obligation_type=catalogue.current_work_type(entry),
            started_on=self.today, due_date=self.today)

    def forged(self, obligation, entry, sale=None):
        sale = sale or self.managed_sale()
        return SaleDocument.objects.create(company=self.co, sale=sale, document_type=entry,
                                           name_snapshot=entry.name, state=OWED, obligation=obligation)

    def assert_refused_everywhere(self, doc, error):
        obligation_status = doc.obligation.status
        before = self.counts()
        for call in (lambda: docs.issue_document(doc, self.manager, self.today),
                     lambda: docs.mark_not_applicable(doc, self.manager, "Not needed"),
                     lambda: docs.correct_issuance(doc, self.manager, "Typo", reference="X")):
            with self.assertRaises(error):
                call()
        doc.refresh_from_db()
        self.assertEqual((doc.state, doc.obligation.status, self.counts()), (OWED, obligation_status, before))

    def test_an_obligation_from_another_sale_is_refused(self):
        entry = self.entry("Real Doc")
        stray = self.stray(self.managed_sale(), entry)                   # right entry, but on a different sale
        self.assert_refused_everywhere(self.forged(stray, entry), docs.SaleDocumentStateError)

    def test_an_obligation_of_another_catalogue_entry_is_refused(self):
        sale = self.managed_sale()
        stray = self.stray(sale, self.entry("Real Doc"))                 # right sale, but another entry's work type
        self.assert_refused_everywhere(self.forged(stray, self.entry("Forged Doc"), sale), docs.SaleDocumentStateError)

    def test_an_obligation_of_another_company_is_refused(self):
        foreign_sale = self.other_sale()
        entry = catalogue.create_document_type(self.other, self.eve, name="Foreign Doc")
        foreign = Obligation.objects.create(
            company=self.other, sale=foreign_sale, obligation_type=catalogue.current_work_type(entry),
            started_on=self.today, due_date=self.today)
        self.assert_refused_everywhere(self.forged(foreign, self.entry("Forged Doc")), PermissionDenied)

    def test_a_document_that_belongs_to_another_company_is_refused(self):
        doc = self.owed()
        SaleDocument.objects.filter(pk=doc.pk).update(company=self.other)
        doc.refresh_from_db()
        with self.assertRaises(PermissionDenied):
            docs.issue_document(doc, self.manager, self.today)

    def test_an_obligation_of_an_earlier_work_type_of_the_same_entry_is_valid(self):
        entry = self.entry("Survey Plan")
        old = self.used_work_type(entry)
        catalogue.rename_document_type(entry, self.manager, "Land Survey")
        sale = self.managed_sale()
        obligation = Obligation.objects.create(company=self.co, sale=sale, obligation_type=old,
                                               started_on=self.today, due_date=self.today)
        doc = SaleDocument.objects.create(company=self.co, sale=sale, document_type=entry,
                                          name_snapshot="Survey Plan", state=OWED, obligation=obligation)
        docs.issue_document(doc, self.manager, self.today)
        doc.refresh_from_db()
        self.assertEqual(doc.state, ISSUED)


class LegacyGenerationTests(DocBase):
    """Decision 4: any sale document for an entry stops legacy generation of ALL that entry's work types."""

    def renamed_legacy_entry(self):
        entry = self.legacy_entry(self.deed)
        self.sale(price=1000, paid=1000)                                 # the Deed type now has an obligation
        catalogue.rename_document_type(entry, self.manager, "Deed of Assignment")
        return entry

    def test_an_owed_document_prevents_a_second_obligation_after_a_rename(self):
        entry = self.renamed_legacy_entry()
        new = catalogue.current_work_type(entry)
        sale = self.sale(price=1000, paid=400)
        doc = self.add(sale, entry)
        self.assertEqual(doc.obligation.obligation_type, new)            # new documents use the CURRENT work type
        result = services.record_payment(sale, self.manager, "600", idempotency_key="k1")
        self.assertEqual(result.obligations_created, [])
        self.assertEqual(Obligation.objects.filter(sale=sale, obligation_type__catalogue_entry=entry).count(), 1)

    def test_without_a_document_legacy_generation_is_exactly_as_before(self):
        self.renamed_legacy_entry()
        sale = self.sale(price=1000, paid=400)
        result = services.record_payment(sale, self.manager, "600", idempotency_key="k1")
        self.assertEqual([o.obligation_type.name for o in result.obligations_created], ["Deed"])

    def test_unrelated_automatic_types_are_still_generated(self):
        entry = self.legacy_entry(self.deed)
        ObligationType.objects.create(company=self.co, name="Legacy Survey", trigger="on_full_payment", sla_days=60)
        sale = self.sale(price=1000, paid=400)
        self.add(sale, entry)
        result = services.record_payment(sale, self.manager, "600", idempotency_key="k1")
        self.assertEqual([o.obligation_type.name for o in result.obligations_created], ["Legacy Survey"])

    def test_reinstating_a_sale_does_not_duplicate_a_documented_entry(self):
        entry = self.renamed_legacy_entry()
        sale = self.sale(price=1000, paid=400)
        self.add(sale, entry)
        services.cancel_sale(sale, self.manager, "defaulted")
        self.assertEqual(services.reinstate_sale(sale, self.manager).obligations_created, [])
        self.assertEqual(Obligation.objects.filter(sale=sale, obligation_type__catalogue_entry=entry).count(), 1)


class ExposureTests(DocBase):
    def test_exposure_follows_every_state_and_each_sale_counts_once(self):
        sale = self.managed_sale(paid=400)
        entry = self.entry()
        self.assertNotIn(sale.pk, self.exposed())
        doc = self.add(sale, entry)
        self.assertEqual(services.exposure(self.co)["sales"], 1)
        self.complete(doc.obligation)                                    # ready to issue: still counted
        self.assertIn(sale.pk, self.exposed())
        second = self.add(sale, self.entry("Second Doc"))               # open work AND an owed document
        self.assertEqual(services.exposure(self.co)["sales"], 1)
        self.assertEqual(services.exposure(self.co)["amount"], 400)
        rows = reports.by_estate(self.co, self.today)
        self.assertEqual(sum(r["amount"] for r in rows), services.exposure(self.co)["amount"])
        docs.issue_document(doc, self.manager, self.today)
        docs.mark_not_applicable(second, self.manager, "Not needed")
        self.assertNotIn(sale.pk, self.exposed())
        self.assertEqual(services.exposure(self.co)["amount"], 0)
        self.assertEqual(sum(r["amount"] for r in reports.by_estate(self.co, self.today)), 0)


class SnapshotAndUndoTests(DocBase):
    def test_a_rename_never_changes_the_name_on_existing_documents(self):
        entry = self.entry("Survey Plan")
        first = self.add(self.managed_sale(), entry)
        catalogue.rename_document_type(entry, self.manager, "Land Survey")
        second = self.add(self.managed_sale(), entry)
        first.refresh_from_db()
        self.assertEqual((first.name_snapshot, second.name_snapshot), ("Survey Plan", "Land Survey"))

    def test_undo_is_refused_once_a_document_is_added_to_an_imported_sale(self):
        batch = ImportBatch.objects.create(company=self.co, created_by=self.manager, filename="x.xlsx",
                                           status=ImportBatch.Status.COMMITTED)
        sale = self.sale(price=1000, paid=400)
        Sale.objects.filter(pk=sale.pk).update(import_batch=batch)
        batch.committed_at = timezone.now() + timedelta(seconds=5)
        batch.save(update_fields=["committed_at"])
        self.assertEqual(undo_blockers(batch), [])
        self.add(Sale.objects.get(pk=sale.pk), self.entry())
        reasons = undo_blockers(batch)
        self.assertEqual(len(reasons), 1)
        self.assertIn("documents", reasons[0])
