"""Phase 8A: a sale created together with its documents (tracker/sale_document_services.add_sale_with_documents).

Only NEW tests live here; every existing test is untouched. The API layer has its own tests in
api/tests/test_sales_desk_documents.py."""
import inspect
from datetime import timedelta
from unittest import mock

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError

from . import document_services as catalogue
from . import sale_document_services as docs
from . import services
from .models import (
    Buyer, DocumentType, Estate, Obligation, ObligationEvent, ObligationType, Payment, Plot, Sale, SaleDocument,
    SaleEvent,
)
from .test_cancel_and_reports import Base

OWED, ISSUED = SaleDocument.State.OWED, SaleDocument.State.ISSUED


class CreationBase(Base):
    def entry(self, name="Survey Plan", **extra):
        return catalogue.create_document_type(self.co, self.manager, name=name, **extra)

    def legacy_entry(self, obligation_type):
        """What the catalogue backfill leaves behind: an entry paired with an existing automatic type."""
        entry = DocumentType.objects.create(company=self.co, name=obligation_type.name)
        ObligationType.objects.filter(pk=obligation_type.pk).update(catalogue_entry=entry, is_current=True)
        return entry

    def create(self, documents, **overrides):
        values = dict(plot_number="N-1", price="1000", sale_date=self.today, estate=self.e1,
                      buyer_name="Ada Obi", amount_paid="400")
        values.update(overrides)
        return docs.add_sale_with_documents(self.co, self.manager, documents, **values)

    def owed(self, entry):
        return {"document_type": entry, "state": "owed"}

    def issued(self, entry, **extra):
        return {"document_type": entry, "state": "issued", "issued_on": self.today - timedelta(days=5), **extra}

    def everything(self):
        models = (Estate, Plot, Buyer, Sale, Payment, SaleEvent, Obligation, ObligationEvent, SaleDocument)
        return tuple(model.objects.count() for model in models)

    def names(self, sale):
        return set(sale.obligations.values_list("obligation_type__name", flat=True))

    def exposed(self):
        return set(services.exposure_sales(self.co).values_list("pk", flat=True))


class ManagedCreationTests(CreationBase):
    def test_the_sale_is_document_managed_and_gets_exactly_the_selected_documents(self):
        survey, occupancy = self.entry("Survey Plan"), self.entry("Certificate of Occupancy")
        receipt = self.entry("Receipt")
        sale = self.create([self.owed(survey), self.issued(occupancy)])
        sale.refresh_from_db()
        self.assertTrue(sale.documents_managed)
        self.assertEqual(set(sale.documents.values_list("name_snapshot", "state")),
                         {("Survey Plan", "owed"), ("Certificate of Occupancy", "issued")})
        self.assertFalse(sale.documents.filter(document_type=receipt).exists())
        # The automatic "Allocation Letter" type exists but is NOT generated; only the owed document has work.
        self.assertEqual(self.names(sale), {"Survey Plan"})

    def test_a_fully_paid_sale_still_gets_no_automatic_obligations(self):
        sale = self.create([self.owed(self.entry("Survey Plan"))], amount_paid="1000")
        self.assertTrue(sale.is_fully_paid)
        self.assertEqual(self.names(sale), {"Survey Plan"})                  # no Allocation Letter, no Deed

    def test_an_owed_document_starts_its_sla_on_the_sale_date(self):
        sale_date = self.today - timedelta(days=100)
        entry = self.entry("Survey Plan", sla_days=30, default_department=self.legal)
        sale = self.create([self.owed(entry)], sale_date=sale_date)
        obligation = sale.obligations.get()
        self.assertEqual((obligation.started_on, obligation.due_date, obligation.department_id, obligation.status),
                         (sale_date, sale_date + timedelta(days=30), self.legal.pk, "not_started"))
        self.assertEqual(obligation.obligation_type, catalogue.current_work_type(entry))
        self.assertIn("Created for the sale's document", obligation.events.get().note)
        document = sale.documents.get()
        self.assertEqual((document.state, document.obligation_id), (OWED, obligation.pk))

    def test_a_document_added_later_to_the_same_sale_still_starts_today(self):
        sale = self.create([self.owed(self.entry("Survey Plan"))], sale_date=self.today - timedelta(days=100))
        later = docs.add_document(sale, self.manager, self.entry("Receipt", sla_days=10), "owed")
        self.assertEqual((later.obligation.started_on, later.obligation.due_date),
                         (self.today, self.today + timedelta(days=10)))

    def test_an_issued_document_records_its_metadata_and_creates_no_obligation(self):
        entry, day = self.entry("Certificate of Occupancy"), self.today - timedelta(days=30)
        sale = self.create([{"document_type": entry, "state": "issued", "issued_on": day, "reference": "  AL   001 "}])
        document = sale.documents.get()
        self.assertEqual((document.state, document.issued_on, document.reference, document.issued_by,
                          document.added_by, document.obligation), (ISSUED, day, "AL 001", self.manager,
                                                                      self.manager, None))
        self.assertIsNotNone(document.issued_at)
        self.assertEqual(sale.obligations.count(), 0)
        event = sale.events.get(action="document_added")
        self.assertEqual(event.actor, self.manager)
        self.assertEqual(event.changes, {"document": [None, "Certificate of Occupancy"], "state": [None, "issued"],
                                         "issued_on": [None, day.isoformat()], "reference": [None, "AL 001"]})

    def test_automatic_document_types_can_be_recorded_as_already_issued(self):
        allocation, deed = self.legacy_entry(self.alloc), self.legacy_entry(self.deed)
        sale = self.create([self.issued(allocation), self.issued(deed)], amount_paid="1000")
        self.assertEqual(sale.obligations.count(), 0)
        self.assertEqual(sale.documents.filter(state=ISSUED).count(), 2)
        self.assertEqual(services.open_obligations(self.co).filter(sale=sale).count(), 0)
        self.assertNotIn(sale.pk, self.exposed())

    def test_an_owed_automatic_document_type_uses_its_own_work_type_from_the_sale_date(self):
        sale_date = self.today - timedelta(days=10)
        sale = self.create([self.owed(self.legacy_entry(self.deed))], sale_date=sale_date)
        obligation = sale.obligations.get()
        self.assertEqual((obligation.obligation_type, obligation.department_id, obligation.started_on,
                          obligation.due_date),
                         (self.deed, self.legal.pk, sale_date, sale_date + timedelta(days=90)))

    def test_a_sale_with_only_issued_documents_stays_managed_and_owed_ones_count_as_exposure(self):
        issued_only = self.create([self.issued(self.entry("Survey Plan"))], plot_number="N-1")
        with_owed = self.create([self.owed(self.entry("Certificate of Occupancy"))], plot_number="N-2",
                                buyer_name="Bola Ade")
        self.assertTrue(Sale.objects.get(pk=issued_only.pk).documents_managed)
        self.assertNotIn(issued_only.pk, self.exposed())
        self.assertIn(with_owed.pk, self.exposed())
        docs.issue_document(with_owed.documents.get(), self.manager, self.today)
        self.assertNotIn(with_owed.pk, self.exposed())

    def test_the_history_has_one_created_event_then_one_document_event_each_in_the_submitted_order(self):
        a, b, c = self.entry("Survey Plan"), self.entry("Certificate of Occupancy"), self.entry("Receipt")
        sale = self.create([self.owed(a), self.issued(b), self.owed(c)])
        self.assertEqual(list(sale.events.values_list("action", flat=True)),
                         ["created", "document_added", "document_added", "document_added"])
        added = list(sale.events.filter(action="document_added"))
        self.assertEqual([event.note.split(":")[0] for event in added],
                         ["Survey Plan", "Certificate of Occupancy", "Receipt"])
        self.assertEqual({event.actor for event in added}, {self.manager})

    def test_later_payments_cancellation_and_reinstatement_generate_nothing(self):
        sale = self.create([self.owed(self.entry("Survey Plan"))], amount_paid="400")
        paid = services.record_payment(sale, self.manager, "600", idempotency_key="k1")
        self.assertEqual(paid.obligations_created, [])
        services.cancel_sale(sale, self.manager, "defaulted")
        self.assertEqual(services.reinstate_sale(sale, self.manager).obligations_created, [])
        self.assertEqual(self.names(sale), {"Survey Plan"})

    def test_catalogue_changes_never_touch_an_existing_sale(self):
        entry = self.entry("Survey Plan")
        sale = self.create([self.owed(entry)])

        def snapshot():
            return (list(sale.documents.values_list("pk", "name_snapshot", "state")),
                    list(sale.obligations.values_list("pk", "obligation_type__name", "status")))

        before = snapshot()
        catalogue.rename_document_type(entry, self.manager, "Land Survey")
        catalogue.set_document_type_active(entry, self.manager, False)
        self.entry("Brand New Type")
        self.assertEqual(snapshot(), before)


class LegacyUnchangedTests(CreationBase):
    def test_a_sale_added_without_documents_is_exactly_the_legacy_sale(self):
        sale = services.add_sale(self.co, self.manager, plot_number="L-1", price="1000", sale_date=self.today,
                                 estate=self.e1, buyer_name="Ada Obi", amount_paid="1000")
        self.assertFalse(Sale.objects.get(pk=sale.pk).documents_managed)
        self.assertEqual(self.names(sale), {"Allocation Letter", "Deed"})

    def test_create_sale_called_positionally_as_the_importer_does_is_legacy(self):
        plot = Plot.objects.create(company=self.co, estate=self.e1, plot_number="I-1")
        buyer = Buyer.objects.create(company=self.co, full_name="Imported")
        sale = services.create_sale(self.co, plot, buyer, 1000, self.today, 0, None, actor=self.manager)
        self.assertFalse(Sale.objects.get(pk=sale.pk).documents_managed)
        self.assertEqual(self.names(sale), {"Allocation Letter"})

    def test_the_new_parameters_are_keyword_only(self):
        for function in (services.create_sale, services.add_sale, docs.add_document):
            name = "started_on" if function is docs.add_document else "documents_managed"
            self.assertEqual(inspect.signature(function).parameters[name].kind, inspect.Parameter.KEYWORD_ONLY)

    def test_add_document_without_the_new_keyword_still_starts_today(self):
        sale = self.sale(price=1000, paid=400)                               # a legacy sale, long ago
        document = docs.add_document(sale, self.manager, self.entry("Survey Plan", sla_days=20), "owed")
        self.assertEqual((document.obligation.started_on, document.obligation.due_date),
                         (self.today, self.today + timedelta(days=20)))

    def test_the_service_refuses_an_empty_list_and_writes_nothing(self):
        before = self.everything()
        with self.assertRaises(ValueError):
            self.create([])
        self.assertEqual(self.everything(), before)


class AtomicityTests(CreationBase):
    def assert_nothing_saved(self, before):
        self.assertEqual(self.everything(), before)

    def test_an_invalid_second_document_saves_neither_the_sale_nor_the_first_document(self):
        good, bad = self.entry("Survey Plan"), self.entry("Receipt")
        catalogue.set_document_type_active(bad, self.manager, False)
        before = self.everything()
        with self.assertRaises(docs.SaleDocumentsError) as caught:
            self.create([self.owed(good), self.owed(bad)], estate=None, estate_name="Brand New Estate")
        error = caught.exception
        self.assertEqual((error.index, error.field, error.is_state_error), (1, "document_type", True))
        self.assert_nothing_saved(before)                    # no sale, estate, plot, buyer, payment, document...

    def test_a_lost_race_while_creating_a_document_rolls_everything_back(self):
        entry = self.entry("Survey Plan")
        before = self.everything()
        with mock.patch.object(SaleDocument.objects, "create", side_effect=IntegrityError("duplicate")):
            with self.assertRaises(docs.SaleDocumentsError) as caught:
                self.create([self.owed(entry)])
        self.assertEqual((caught.exception.index, caught.exception.is_state_error), (0, True))
        self.assert_nothing_saved(before)

    def test_a_crash_while_creating_the_second_document_rolls_back_the_sale_and_the_first_document(self):
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
                self.create([self.owed(a), self.issued(b)], estate=None, estate_name="Brand New Estate")
        self.assertEqual(len(calls), 2)
        self.assert_nothing_saved(before)

    def test_a_failure_recording_the_audit_of_an_issued_document_rolls_everything_back(self):
        entry = self.entry("Certificate of Occupancy")
        before = self.everything()
        with mock.patch("tracker.sale_document_services._log_sale", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.create([self.issued(entry, reference="AL-001")])
        self.assert_nothing_saved(before)

    def test_a_failure_creating_an_obligation_audit_rolls_everything_back(self):
        entry = self.entry("Survey Plan")
        before = self.everything()
        with mock.patch.object(ObligationEvent.objects, "create", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.create([self.owed(entry)])
        self.assert_nothing_saved(before)

    def test_an_occupied_plot_is_refused_before_any_document_and_names_nothing_new(self):
        existing = self.sale(price=1000, paid=400)
        entry = self.entry("Survey Plan")
        before = self.everything()
        with self.assertRaises(services.PlotOccupied):
            self.create([self.owed(entry)], plot_number=existing.plot.plot_number, estate=existing.plot.estate)
        self.assert_nothing_saved(before)

    def test_bad_sale_input_is_refused_as_before_and_creates_no_document(self):
        entry = self.entry("Survey Plan")
        before = self.everything()
        with self.assertRaises(ValueError) as caught:
            self.create([self.owed(entry)], price="0")
        self.assertNotIsInstance(caught.exception, docs.SaleDocumentsError)
        self.assert_nothing_saved(before)

    def test_another_companys_document_type_is_refused_and_nothing_is_saved(self):
        foreign = catalogue.create_document_type(self.other, self.eve, name="Foreign Doc")
        before = self.everything()
        with self.assertRaises(PermissionDenied):
            self.create([self.owed(foreign)])
        self.assert_nothing_saved(before)

    def test_service_level_errors_name_the_position_and_the_field(self):
        a, b, c = self.entry("Survey Plan"), self.entry("Certificate of Occupancy"), self.entry("Receipt")
        ObligationType.objects.filter(catalogue_entry=c).update(is_current=False)       # nothing to track the work
        cases = [
            ("an owed document with an issue date", [self.owed(a), {**self.owed(b), "issued_on": self.today}],
             1, "issued_on", False),
            ("an issued document without a date", [{"document_type": a, "state": "issued"}], 0, "issued_on", False),
            ("a state that cannot be chosen", [{"document_type": a, "state": "not_applicable"}], 0, "state", False),
            ("an owed type without a current work type", [self.owed(a), self.owed(c)], 1, "document_type", True),
        ]
        for label, documents, index, field, is_state_error in cases:
            before = self.everything()
            with self.assertRaises(docs.SaleDocumentsError, msg=label) as caught:
                self.create(documents)
            error = caught.exception
            self.assertEqual((error.index, error.field, error.is_state_error), (index, field, is_state_error), label)
            self.assert_nothing_saved(before)
