from datetime import date
from io import StringIO

from django.core.management import call_command
from django.db import IntegrityError, connection, transaction

from .models import (
    Buyer, DocumentType, DocumentTypeEvent, ObligationType, Plot, Sale, SaleDocument,
)
from .test_cancel_and_reports import Base


class DocumentSchemaTests(Base):
    def doc_type(self, name="Survey Plan", company=None):
        return DocumentType.objects.create(company=company or self.co, name=name)

    def sale_doc(self, sale, doc_type, **extra):
        values = dict(company=self.co, sale=sale, document_type=doc_type, name_snapshot=doc_type.name,
                      state=SaleDocument.State.ISSUED, issued_on=self.today)
        values.update(extra)
        return SaleDocument.objects.create(**values)

    def refused(self, **extra):
        sale = self.sale()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.sale_doc(sale, self.doc_type(), **extra)

    # ---- catalogue ----

    def test_a_document_name_is_unique_per_company_ignoring_case(self):
        self.doc_type("Survey Plan")
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.doc_type("survey PLAN")
        self.assertTrue(self.doc_type("Survey Plan", company=self.other).pk)        # another company may reuse it

    def test_catalogue_defaults(self):
        entry = self.doc_type()
        self.assertEqual((entry.is_active, entry.sort_order, entry.category, entry.description), (True, 0, "", ""))

    def test_multiple_obligation_types_can_share_one_catalogue_entry(self):
        entry = self.doc_type("Deed")
        ObligationType.objects.filter(pk=self.deed.pk).update(catalogue_entry=entry)
        ObligationType.objects.filter(pk=self.alloc.pk).update(catalogue_entry=entry)
        self.deed.refresh_from_db()
        self.alloc.refresh_from_db()
        self.assertEqual(self.deed.catalogue_entry, entry)
        self.assertEqual(self.alloc.catalogue_entry, entry)
        self.assertEqual(set(entry.obligation_types.values_list("pk", flat=True)),
                         {self.deed.pk, self.alloc.pk})

    # ---- sale documents ----

    def test_one_row_per_sale_and_document_type(self):
        sale, entry = self.sale(), self.doc_type()
        self.sale_doc(sale, entry)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.sale_doc(sale, entry)

    def test_an_owed_document_needs_an_obligation(self):
        self.refused(state=SaleDocument.State.OWED, issued_on=None, obligation=None)

    def test_an_issued_document_needs_an_issue_date(self):
        self.refused(state=SaleDocument.State.ISSUED, issued_on=None)

    def test_a_not_applicable_document_needs_a_reason(self):
        self.refused(state=SaleDocument.State.NOT_APPLICABLE, issued_on=None, not_applicable_reason="")

    def test_valid_rows_of_every_state_are_accepted(self):
        sale = self.sale()
        obligation = sale.obligations.get(obligation_type=self.deed)
        owed = self.sale_doc(sale, self.doc_type("A"), state=SaleDocument.State.OWED, issued_on=None,
                             obligation=obligation)
        issued = self.sale_doc(sale, self.doc_type("B"), issued_on=date(2026, 1, 5), reference="REF-1")
        skipped = self.sale_doc(sale, self.doc_type("C"), state=SaleDocument.State.NOT_APPLICABLE, issued_on=None,
                                not_applicable_reason="Buyer already holds it")
        self.assertEqual(obligation.sale_document, owed)                       # reverse link from the obligation
        self.assertEqual({d.state for d in sale.documents.all()}, {"owed", "issued", "not_applicable"})
        self.assertEqual((issued.obligation, skipped.obligation), (None, None))

    # ---- audit rows ----

    def test_catalogue_history_cannot_be_changed_or_deleted(self):
        event = DocumentTypeEvent.objects.create(
            company=self.co, document_type=self.doc_type(), actor=self.manager,
            action=DocumentTypeEvent.Action.CREATED, changes={"name": [None, "Survey Plan"]})
        event.note = "tampered"
        with self.assertRaises(ValueError):
            event.save()
        with self.assertRaises(ValueError):
            event.delete()
        event.refresh_from_db()
        self.assertEqual(event.note, "")

    # ---- new columns on existing tables ----

    def test_new_columns_default_to_the_legacy_values(self):
        self.assertTrue(self.alloc.auto_generate)
        self.assertIsNone(self.alloc.catalogue_entry_id)
        self.assertFalse(self.sale().documents_managed)

    def test_the_database_supplies_the_new_columns_when_old_code_omits_them(self):
        """Old application code that is still running just after the migration does not know the new columns.
        Their database defaults must let it insert rows anyway."""
        qn = connection.ops.quote_name
        plot = Plot.objects.create(company=self.co, estate=self.e1, plot_number="RAW-1")
        buyer = Buyer.objects.create(company=self.co, full_name="Raw Buyer")
        with connection.cursor() as cursor:
            cursor.execute(
                f"INSERT INTO {qn(ObligationType._meta.db_table)} (company_id, name, trigger, sla_days, is_active) "
                "VALUES (%s, %s, %s, %s, %s)", [self.co.pk, "Raw Type", "on_sale", 30, True])
            cursor.execute(
                f"INSERT INTO {qn(Sale._meta.db_table)} "
                "(company_id, plot_id, buyer_id, price, amount_paid, sale_date, status, cancel_reason) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                [self.co.pk, plot.pk, buyer.pk, "100.00", "0", self.today, "active", ""])
        self.assertTrue(ObligationType.objects.get(name="Raw Type").auto_generate)
        self.assertFalse(Sale.objects.get(plot=plot).documents_managed)

    def test_the_model_changes_are_fully_covered_by_migrations(self):
        call_command("makemigrations", "--check", "--dry-run", stdout=StringIO(), stderr=StringIO())
