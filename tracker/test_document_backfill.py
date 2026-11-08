import importlib

from django.apps import apps

from .models import (
    DocumentType, DocumentTypeEvent, Obligation, ObligationType, Sale, SaleDocument,
)
from .test_cancel_and_reports import Base

backfill = importlib.import_module("tracker.migrations.0010_backfill_document_catalogue").backfill_document_catalogue

FIELDS = ("id", "company_id", "name", "trigger", "sla_days", "default_department_id", "is_active", "auto_generate")


def snapshot():
    return list(ObligationType.objects.order_by("id").values_list(*FIELDS))


class BackfillTests(Base):
    """Runs the real 0010 function against live models. Base gives two companies; Allocation Letter (on_sale,
    30 days, no department) and Deed (on_full_payment, 90 days, Legal) already exist."""

    def setUp(self):
        super().setUp()
        self.old = ObligationType.objects.create(
            company=self.co, name="Old Doc", trigger="on_full_payment", sla_days=15, is_active=False)
        self.catalogue_only = ObligationType.objects.create(
            company=self.other, name="Receipt", trigger="on_sale", sla_days=7, is_active=True, auto_generate=False)

    def run_backfill(self):
        backfill(apps, None)

    def test_one_catalogue_entry_per_type_with_the_same_company_name_and_active_flag(self):
        self.run_backfill()
        self.assertEqual(DocumentType.objects.count(), ObligationType.objects.count())
        for t in ObligationType.objects.all():
            entry = t.catalogue_entry
            self.assertIsNotNone(entry, t.name)
            self.assertEqual((entry.company_id, entry.name, entry.is_active), (t.company_id, t.name, t.is_active))
            self.assertEqual(entry.obligation_types.get(), t)                   # reverse lookup works

    def test_every_obligation_type_field_is_preserved(self):
        before = snapshot()
        self.run_backfill()
        self.assertEqual(snapshot(), before)
        legal_deed = ObligationType.objects.get(pk=self.deed.pk)
        self.assertEqual((legal_deed.trigger, legal_deed.sla_days, legal_deed.default_department_id),
                         ("on_full_payment", 90, self.legal.pk))
        self.assertFalse(ObligationType.objects.get(pk=self.old.pk).is_active)               # inactive stays inactive
        self.assertFalse(ObligationType.objects.get(pk=self.catalogue_only.pk).auto_generate)  # and so does False

    def test_one_creation_event_per_new_entry(self):
        self.run_backfill()
        events = DocumentTypeEvent.objects.all()
        self.assertEqual(events.count(), ObligationType.objects.count())
        for event in events:
            self.assertEqual((event.action, event.actor_id), ("created", None))
            self.assertEqual(event.company_id, event.document_type.company_id)
            self.assertIn("existing obligation type", event.note)
            self.assertEqual(event.changes["name"], [None, event.document_type.name])

    def test_sales_obligations_and_documents_are_not_touched(self):
        self.sale(price=1000, paid=1000)
        self.sale(price=1000, paid=400)
        before = (
            list(Sale.objects.order_by("id").values_list("id", "price", "amount_paid", "status", "documents_managed")),
            list(Obligation.objects.order_by("id").values_list("id", "obligation_type_id", "status", "due_date")),
            SaleDocument.objects.count(),
        )
        self.run_backfill()
        after = (
            list(Sale.objects.order_by("id").values_list("id", "price", "amount_paid", "status", "documents_managed")),
            list(Obligation.objects.order_by("id").values_list("id", "obligation_type_id", "status", "due_date")),
            SaleDocument.objects.count(),
        )
        self.assertEqual(after, before)
        self.assertEqual(SaleDocument.objects.count(), 0)

    def test_running_it_twice_changes_nothing_more(self):
        self.run_backfill()
        entries = sorted(DocumentType.objects.values_list("id", "company_id", "name", "is_active"))
        events = DocumentTypeEvent.objects.count()
        self.run_backfill()
        self.assertEqual(sorted(DocumentType.objects.values_list("id", "company_id", "name", "is_active")), entries)
        self.assertEqual(DocumentTypeEvent.objects.count(), events)

    def test_a_type_that_already_has_an_entry_is_skipped(self):
        existing = DocumentType.objects.create(company=self.co, name="Deed", description="kept as it is")
        ObligationType.objects.filter(pk=self.deed.pk).update(catalogue_entry=existing)
        self.run_backfill()
        self.assertEqual(DocumentType.objects.filter(company=self.co, name="Deed").count(), 1)
        existing.refresh_from_db()
        self.assertEqual(existing.description, "kept as it is")
        self.assertEqual(DocumentType.objects.count(), ObligationType.objects.count())
        self.assertEqual(DocumentTypeEvent.objects.count(), ObligationType.objects.count() - 1)

    def test_names_that_differ_only_by_case_abort_and_change_nothing(self):
        ObligationType.objects.create(company=self.co, name="deed", trigger="on_sale", sla_days=1)
        before = snapshot()
        with self.assertRaises(RuntimeError) as caught:
            self.run_backfill()
        message = str(caught.exception)
        self.assertIn("A Homes", message)
        self.assertIn("'Deed'", message)
        self.assertIn("'deed'", message)
        self.assertEqual((DocumentType.objects.count(), DocumentTypeEvent.objects.count()), (0, 0))
        self.assertEqual(snapshot(), before)
        self.assertFalse(ObligationType.objects.filter(catalogue_entry__isnull=False).exists())

    def test_a_clash_with_an_existing_catalogue_entry_also_aborts(self):
        DocumentType.objects.create(company=self.co, name="DEED")          # an unlinked entry with the same name
        with self.assertRaises(RuntimeError):
            self.run_backfill()
        self.assertEqual(DocumentTypeEvent.objects.count(), 0)
        self.assertFalse(ObligationType.objects.filter(catalogue_entry__isnull=False).exists())

    def test_nothing_to_do_is_fine(self):
        ObligationType.objects.all().delete()
        self.run_backfill()
        self.assertEqual(DocumentType.objects.count(), 0)
