from . import services
from .models import DocumentType, Obligation, ObligationType, Sale, SaleDocument
from .test_cancel_and_reports import Base


class GenerationGuardTests(Base):
    """generate_obligations: legacy sales behave exactly as before; managed sales, catalogue-only types and
    documents that are already settled never produce automatic obligations."""

    def names(self, sale):
        return set(sale.obligations.values_list("obligation_type__name", flat=True))

    def pay_in_full(self, sale, key="k1"):
        return services.record_payment(sale, self.manager, sale.price - sale.amount_paid, idempotency_key=key)

    def manage(self, sale):
        """Make a sale document-managed and clear what creation generated, as a managed sale would start."""
        Sale.objects.filter(pk=sale.pk).update(documents_managed=True)
        sale.obligations.all().delete()
        return Sale.objects.get(pk=sale.pk)

    def entry_for(self, obligation_type, name=None):
        entry = DocumentType.objects.create(company=self.co, name=name or obligation_type.name)
        ObligationType.objects.filter(pk=obligation_type.pk).update(catalogue_entry=entry)
        return entry

    def settle(self, sale, entry, state, **extra):
        values = dict(company=self.co, sale=sale, document_type=entry, name_snapshot=entry.name, state=state)
        values.update(extra)
        return SaleDocument.objects.create(**values)

    # ---- unchanged legacy behaviour ----

    def test_legacy_generation_is_unchanged_by_default(self):
        sale = self.sale(price=1000, paid=400)
        self.assertEqual(self.names(sale), {"Allocation Letter"})
        result = self.pay_in_full(sale)
        self.assertEqual([o.obligation_type.name for o in result.obligations_created], ["Deed"])
        self.assertEqual(self.names(sale), {"Allocation Letter", "Deed"})

    def test_inactive_types_stay_excluded(self):
        ObligationType.objects.filter(pk=self.alloc.pk).update(is_active=False)
        self.assertEqual(self.names(self.sale(price=1000, paid=400)), set())

    # ---- managed sales ----

    def test_a_managed_sale_generates_nothing(self):
        sale = self.manage(self.sale(price=1000, paid=400))
        self.assertEqual(services.generate_obligations(sale), [])
        self.assertEqual(sale.obligations.count(), 0)

    def test_a_managed_sale_gets_nothing_on_payment_or_reinstatement(self):
        sale = self.manage(self.sale(price=1000, paid=400))
        result = self.pay_in_full(sale)
        self.assertEqual(result.obligations_created, [])
        services.cancel_sale(sale, self.manager, "defaulted")
        reinstated = services.reinstate_sale(sale, self.manager)
        self.assertEqual(reinstated.obligations_created, [])
        self.assertEqual(Obligation.objects.filter(sale=sale).count(), 0)

    # ---- catalogue-only types ----

    def test_catalogue_only_types_are_never_generated(self):
        ObligationType.objects.create(
            company=self.co, name="Receipt", trigger="on_sale", sla_days=30, auto_generate=False)
        sale = self.sale(price=1000, paid=400)
        self.assertEqual(self.names(sale), {"Allocation Letter"})
        self.pay_in_full(sale)
        services.cancel_sale(sale, self.manager, "defaulted")
        services.reinstate_sale(sale, self.manager)
        self.assertEqual(self.names(sale), {"Allocation Letter", "Deed"})

    # ---- settled documents ----

    def test_an_issued_document_suppresses_its_legacy_obligation_even_under_another_name(self):
        sale = self.sale(price=1000, paid=400)
        entry = self.entry_for(self.deed, name="Deed of Assignment")          # matched through the link, not the name
        self.settle(sale, entry, SaleDocument.State.ISSUED, issued_on=self.today)
        result = self.pay_in_full(sale)
        self.assertEqual(result.obligations_created, [])
        self.assertEqual(self.names(sale), {"Allocation Letter"})

    def test_a_not_applicable_document_suppresses_its_legacy_obligation(self):
        sale = self.sale(price=1000, paid=400)
        entry = self.entry_for(self.deed)
        self.settle(sale, entry, SaleDocument.State.NOT_APPLICABLE, not_applicable_reason="Not required")
        self.assertEqual(self.pay_in_full(sale).obligations_created, [])
        self.assertEqual(self.names(sale), {"Allocation Letter"})

    def test_it_also_holds_when_the_sale_is_reinstated(self):
        sale = self.sale(price=1000, paid=1000)
        sale.obligations.filter(obligation_type=self.deed).delete()
        self.settle(sale, self.entry_for(self.deed), SaleDocument.State.ISSUED, issued_on=self.today)
        services.cancel_sale(sale, self.manager, "defaulted")
        self.assertEqual(services.reinstate_sale(sale, self.manager).obligations_created, [])

    def test_a_settled_document_of_another_entry_suppresses_nothing(self):
        sale = self.sale(price=1000, paid=400)
        other_entry = DocumentType.objects.create(company=self.co, name="Survey Plan")
        self.settle(sale, other_entry, SaleDocument.State.ISSUED, issued_on=self.today)
        self.assertEqual([o.obligation_type.name for o in self.pay_in_full(sale).obligations_created], ["Deed"])

    def test_an_owed_document_does_not_duplicate_its_obligation(self):
        sale = self.sale(price=1000, paid=400)
        entry = self.entry_for(self.deed)
        obligation = Obligation.objects.create(
            company=self.co, sale=sale, obligation_type=self.deed, started_on=self.today, due_date=self.today)
        self.settle(sale, entry, SaleDocument.State.OWED, obligation=obligation)
        result = self.pay_in_full(sale)
        self.assertEqual(result.obligations_created, [])
        self.assertEqual(Obligation.objects.filter(sale=sale, obligation_type=self.deed).count(), 1)
