from datetime import timedelta
from types import SimpleNamespace

from django.utils import timezone

from api.views.imports import ImportBatchViewSet
from importer.commit import undo_batch, undo_blockers
from importer.errors import UndoBlocked
from importer.validation import validate_import
from tracker.models import (
    DocumentType, ImportBatch, ObligationType, Sale, SaleDocument, SaleEvent,
)
from tracker.test_cancel_and_reports import Base

HEADERS = ["Estate", "Plot", "Buyer", "Price", "Paid", "Doc"]
BASE_MAPPING = {"estate": "Estate", "plot_number": "Plot", "buyer_name": "Buyer", "price": "Price",
                "amount_paid": "Paid"}


class DocumentMappingTests(Base):
    """Only active, auto-generated types can be mapped to spreadsheet columns."""

    def type_names(self):
        return ImportBatchViewSet()._type_names(SimpleNamespace(user=self.manager))

    def mapping_problems(self, type_name):
        batch = ImportBatch.objects.create(
            company=self.co, created_by=self.manager, filename="x.xlsx", headers=HEADERS,
            rows=[{"n": 2, "v": ["Green Valley", "A-1", "Ada Obi", "1000", "500", "done"]}],
            mapping={**BASE_MAPPING, "documents": {type_name: "Doc"}})
        report, _ = validate_import(batch)
        return report["mapping_problems"]

    def test_existing_active_types_are_still_offered(self):
        self.assertEqual(self.type_names(), {"Allocation Letter", "Deed"})
        self.assertEqual(self.mapping_problems("Deed"), [])

    def test_a_catalogue_only_type_is_not_offered_and_cannot_be_mapped(self):
        ObligationType.objects.create(
            company=self.co, name="Receipt", trigger="on_sale", sla_days=30, auto_generate=False)
        self.assertNotIn("Receipt", self.type_names())
        problems = self.mapping_problems("Receipt")
        self.assertEqual(len(problems), 1)
        self.assertIn("'Receipt' is not an active obligation type", problems[0])

    def test_inactive_types_stay_excluded(self):
        ObligationType.objects.filter(pk=self.deed.pk).update(is_active=False)
        self.assertEqual(self.type_names(), {"Allocation Letter"})
        self.assertIn("'Deed' is not an active obligation type", self.mapping_problems("Deed")[0])


class ImportUndoDocumentTests(Base):
    def imported_sale(self):
        batch = ImportBatch.objects.create(company=self.co, created_by=self.manager, filename="x.xlsx",
                                           status=ImportBatch.Status.COMMITTED)
        sale = self.sale(price=1000, paid=400)
        Sale.objects.filter(pk=sale.pk).update(import_batch=batch)
        batch.committed_at = timezone.now() + timedelta(seconds=5)       # after everything the import wrote
        batch.save(update_fields=["committed_at"])
        return batch, sale

    def add_document(self, sale):
        entry = DocumentType.objects.create(company=self.co, name="Survey Plan")
        return SaleDocument.objects.create(
            company=self.co, sale=sale, document_type=entry, name_snapshot="Survey Plan",
            state=SaleDocument.State.ISSUED, issued_on=self.today)

    def test_undo_still_works_when_nothing_was_added(self):
        batch, sale = self.imported_sale()
        self.assertEqual(undo_blockers(batch), [])
        undo_batch(batch, self.manager)
        batch.refresh_from_db()
        self.assertEqual(batch.status, ImportBatch.Status.UNDONE)
        self.assertFalse(Sale.objects.filter(pk=sale.pk).exists())

    def test_undo_is_refused_when_a_document_was_added_to_an_imported_sale(self):
        batch, sale = self.imported_sale()
        document = self.add_document(sale)
        reasons = undo_blockers(batch)
        self.assertEqual(len(reasons), 1)
        self.assertIn("documents", reasons[0])
        with self.assertRaises(UndoBlocked):
            undo_batch(batch, self.manager)
        batch.refresh_from_db()
        self.assertEqual(batch.status, ImportBatch.Status.COMMITTED)
        self.assertTrue(Sale.objects.filter(pk=sale.pk).exists())
        self.assertTrue(SaleDocument.objects.filter(pk=document.pk).exists())          # nothing was destroyed

    def test_documents_on_other_sales_do_not_block_the_undo(self):
        batch, _ = self.imported_sale()
        self.add_document(self.sale(price=500, paid=500))                  # a sale that was not imported
        self.assertEqual(undo_blockers(batch), [])

    def test_the_new_document_history_actions_are_protected_too(self):
        batch, sale = self.imported_sale()
        for action in (SaleEvent.Action.DOCUMENT_ADDED, SaleEvent.Action.DOCUMENT_ISSUED,
                       SaleEvent.Action.DOCUMENT_ISSUANCE_CORRECTED, SaleEvent.Action.DOCUMENT_NOT_APPLICABLE):
            event = SaleEvent.objects.create(company=self.co, sale=sale, actor=self.manager, action=action)
            SaleEvent.objects.filter(pk=event.pk).update(created_at=batch.committed_at + timedelta(minutes=1))
            self.assertEqual(len(undo_blockers(batch)), 1, action)
            SaleEvent.objects.filter(pk=event.pk).delete()
        self.assertEqual(undo_blockers(batch), [])
