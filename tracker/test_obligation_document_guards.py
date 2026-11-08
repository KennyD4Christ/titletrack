import unittest

from django.urls import NoReverseMatch, reverse
from rest_framework.test import APIClient

from . import document_services as catalogue
from . import sale_document_services as docs
from . import services
from .models import Obligation, ObligationEvent, SaleDocument
from .test_cancel_and_reports import Base

OPEN_STATUSES = ("not_started", "in_progress", "awaiting_external")
ALL_STATUSES = OPEN_STATUSES + ("completed", "not_applicable")


def _obligation_api_available():
    try:
        reverse("obligation-detail", args=[1])
        reverse("obligation-bulk-update")
        return True
    except NoReverseMatch:
        return False


class GuardBase(Base):
    def managed_sale(self):
        sale = self.sale(price=1000, paid=400)
        sale.obligations.all().delete()
        type(sale).objects.filter(pk=sale.pk).update(documents_managed=True)
        return type(sale).objects.get(pk=sale.pk)

    def document(self):
        self.documents_made = getattr(self, "documents_made", 0) + 1
        entry = catalogue.create_document_type(self.co, self.manager, name=f"Document {self.documents_made}")
        return docs.add_document(self.managed_sale(), self.manager, entry, "owed")

    def owed(self):
        return self.document()

    def issued(self):
        doc = self.document()
        docs.issue_document(doc, self.manager, self.today)
        return SaleDocument.objects.get(pk=doc.pk)

    def not_applicable(self):
        doc = self.document()
        docs.mark_not_applicable(doc, self.manager, "Not required")
        return SaleDocument.objects.get(pk=doc.pk)

    def set_status(self, obligation, status):
        return services.update_obligation(obligation, self.manager, status=status)

    def force_status(self, obligation, status):
        Obligation.objects.filter(pk=obligation.pk).update(status=status)
        return Obligation.objects.get(pk=obligation.pk)

    def refused(self, obligation, status):
        before = (Obligation.objects.get(pk=obligation.pk).status, ObligationEvent.objects.count())
        with self.assertRaises(services.SaleStateError, msg=status):
            self.set_status(obligation, status)
        self.assertEqual((Obligation.objects.get(pk=obligation.pk).status, ObligationEvent.objects.count()), before)


class DocumentGuardTests(GuardBase):
    def test_an_owed_documents_obligation_cannot_be_set_to_not_applicable(self):
        doc = self.owed()
        self.refused(doc.obligation, "not_applicable")
        doc.refresh_from_db()
        self.assertEqual(doc.state, SaleDocument.State.OWED)

    def test_an_owed_documents_obligation_can_move_through_the_other_statuses_and_back(self):
        doc = self.owed()
        for status in ("in_progress", "awaiting_external", "completed", "in_progress", "not_started", "completed"):
            self.set_status(doc.obligation, status)
            self.assertEqual(Obligation.objects.get(pk=doc.obligation_id).status, status)
        doc.refresh_from_db()
        self.assertEqual(doc.state, SaleDocument.State.OWED)             # preparing it never issued it

    def test_an_issued_documents_obligation_stays_completed(self):
        doc = self.issued()
        for status in ("not_started", "in_progress", "awaiting_external", "not_applicable"):
            self.refused(doc.obligation, status)
        self.set_status(doc.obligation, "completed")                      # no change: not a violation
        self.assertEqual(Obligation.objects.get(pk=doc.obligation_id).status, "completed")

    def test_an_issued_documents_open_obligation_may_still_be_completed(self):
        doc = self.issued()
        self.force_status(doc.obligation, "in_progress")                  # as if left open by an earlier fault
        self.set_status(doc.obligation, "completed")
        self.assertEqual(Obligation.objects.get(pk=doc.obligation_id).status, "completed")

    def test_a_not_applicable_documents_obligation_stays_not_applicable(self):
        doc = self.not_applicable()
        for status in ("not_started", "in_progress", "awaiting_external", "completed"):
            self.refused(doc.obligation, status)
        self.set_status(doc.obligation, "not_applicable")                 # no change: not a violation

    def test_other_fields_stay_editable_in_every_document_state(self):
        for doc in (self.owed(), self.issued(), self.not_applicable()):
            updated = services.update_obligation(
                doc.obligation, self.sarah, note="Chased the registry", owner=self.sarah, stage="Registry",
                next_action="Call on Monday")
            self.assertEqual((updated.owner, updated.stage, updated.next_action),
                             (self.sarah, "Registry", "Call on Monday"), doc.state)
            self.assertEqual(updated.events.latest("id").note, "Chased the registry")

    def test_an_obligation_without_a_sale_document_is_not_affected(self):
        sale = self.sale(price=1000, paid=1000)                           # ordinary legacy obligations
        for obligation in sale.obligations.all():
            for status in ALL_STATUSES:
                self.set_status(obligation, status)
                self.assertEqual(Obligation.objects.get(pk=obligation.pk).status, status)


class BulkGuardTests(GuardBase):
    def test_a_bulk_update_with_one_refused_obligation_changes_nothing(self):
        ordinary = self.sale(price=1000, paid=400).obligations.get()
        doc = self.owed()
        events = ObligationEvent.objects.count()
        with self.assertRaises(services.SaleStateError):
            services.bulk_update_obligations([ordinary.pk, doc.obligation_id], self.manager, status="not_applicable")
        self.assertEqual(Obligation.objects.get(pk=ordinary.pk).status, "not_started")      # rolled back as a whole
        self.assertEqual(Obligation.objects.get(pk=doc.obligation_id).status, "not_started")
        self.assertEqual(ObligationEvent.objects.count(), events)

    def test_a_bulk_update_that_respects_the_documents_still_works(self):
        ordinary = self.sale(price=1000, paid=400).obligations.get()
        doc = self.owed()
        updated = services.bulk_update_obligations([ordinary.pk, doc.obligation_id], self.manager,
                                                   status="in_progress")
        self.assertEqual(len(updated), 2)
        self.assertEqual({o.status for o in updated}, {"in_progress"})


@unittest.skipUnless(_obligation_api_available(), "the obligation API routes are not available here")
class ObligationApiGuardTests(GuardBase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(self.manager)

    def patch(self, obligation, payload):
        return self.client.patch(reverse("obligation-detail", args=[obligation.pk]), payload, format="json")

    def test_the_obligation_screen_cannot_contradict_the_document(self):
        owed, issued, skipped = self.owed(), self.issued(), self.not_applicable()
        self.assertEqual(self.patch(owed.obligation, {"status": "not_applicable"}).status_code, 409)
        self.assertEqual(self.patch(issued.obligation, {"status": "in_progress"}).status_code, 409)
        self.assertEqual(self.patch(skipped.obligation, {"status": "completed"}).status_code, 409)
        self.assertEqual(Obligation.objects.get(pk=owed.obligation_id).status, "not_started")
        self.assertEqual(Obligation.objects.get(pk=issued.obligation_id).status, "completed")
        self.assertEqual(Obligation.objects.get(pk=skipped.obligation_id).status, "not_applicable")

    def test_permitted_edits_still_work_through_the_api(self):
        owed = self.owed()
        self.assertEqual(self.patch(owed.obligation, {"status": "in_progress"}).status_code, 200)
        self.assertEqual(self.patch(owed.obligation, {"next_action": "Draft it", "note": "Started"}).status_code, 200)
        ordinary = self.sale(price=1000, paid=400).obligations.get()
        self.assertEqual(self.patch(ordinary, {"status": "not_applicable"}).status_code, 200)

    def test_a_bulk_update_through_the_api_is_all_or_nothing(self):
        ordinary = self.sale(price=1000, paid=400).obligations.get()
        owed = self.owed()
        url = reverse("obligation-bulk-update")
        response = self.client.post(url, {"ids": [ordinary.pk, owed.obligation_id], "status": "not_applicable"},
                                    format="json")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(Obligation.objects.get(pk=ordinary.pk).status, "not_started")
        self.assertEqual(Obligation.objects.get(pk=owed.obligation_id).status, "not_started")
        ok = self.client.post(url, {"ids": [ordinary.pk, owed.obligation_id], "status": "in_progress"}, format="json")
        self.assertEqual(ok.status_code, 200)
