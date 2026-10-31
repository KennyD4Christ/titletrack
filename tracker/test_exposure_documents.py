from decimal import Decimal

from . import reports, services
from .models import Buyer, DocumentType, Estate, Plot, Sale, SaleDocument
from .test_cancel_and_reports import Base


class ExposureDocumentTests(Base):
    """Exposure = money collected on ACTIVE sales with an open obligation OR an owed document, each sale once.
    Issued and not-applicable documents never count by themselves."""

    def doc(self, sale, state, obligation=None, **extra):
        entry = DocumentType.objects.create(company=sale.company, name=f"Doc {DocumentType.objects.count()}")
        values = dict(company=sale.company, sale=sale, document_type=entry, name_snapshot=entry.name, state=state,
                      obligation=obligation)
        values.update(extra)
        return SaleDocument.objects.create(**values)

    def owed(self, sale, obligation=None):
        return self.doc(sale, SaleDocument.State.OWED, obligation or sale.obligations.get(obligation_type=self.deed))

    def issued(self, sale):
        return self.doc(sale, SaleDocument.State.ISSUED, issued_on=self.today)

    def not_applicable(self, sale):
        return self.doc(sale, SaleDocument.State.NOT_APPLICABLE, not_applicable_reason="Not required")

    def finish_work(self, sale):
        for obligation in sale.obligations.all():
            services.update_obligation(obligation, self.manager, status="completed")

    def figures(self, company=None):
        e = services.exposure(company or self.co)
        return e["amount"], e["sales"], e["buyers"]

    # ---- the scenarios ----

    def test_an_open_obligation_counts(self):
        self.sale(price=1000, paid=1000)
        self.assertEqual(self.figures(), (Decimal("1000.00"), 1, 1))

    def test_an_owed_document_counts_even_when_its_preparation_obligation_is_completed(self):
        sale = self.sale(price=1000, paid=1000)
        self.finish_work(sale)
        self.assertEqual(self.figures(), (Decimal("0"), 0, 0))                  # finished work alone: not counted
        self.owed(sale)                                                           # ready to issue, not yet issued
        self.assertEqual(self.figures(), (Decimal("1000.00"), 1, 1))

    def test_an_issued_document_no_longer_counts(self):
        sale = self.sale(price=1000, paid=1000)
        self.finish_work(sale)
        self.issued(sale)
        self.assertEqual(self.figures(), (Decimal("0"), 0, 0))

    def test_an_issued_document_does_not_hide_other_open_work(self):
        sale = self.sale(price=1000, paid=1000)
        self.issued(sale)
        self.assertEqual(self.figures()[1], 1)                                    # the open obligations still count

    def test_a_not_applicable_document_does_not_count(self):
        sale = self.sale(price=1000, paid=1000)
        self.finish_work(sale)
        self.not_applicable(sale)
        self.assertEqual(self.figures(), (Decimal("0"), 0, 0))

    def test_a_sale_with_open_work_and_an_owed_document_is_counted_once(self):
        sale = self.sale(price=1000, paid=1000)
        self.owed(sale)                                                           # its obligation is still open too
        self.assertEqual(self.figures(), (Decimal("1000.00"), 1, 1))
        self.assertEqual(services.exposure_sales(self.co).count(), 1)
        self.assertEqual(len(set(services.exposure_sales(self.co).values_list("pk", flat=True))), 1)

    def test_cancelled_sales_are_excluded(self):
        sale = self.sale(price=1000, paid=1000)
        self.finish_work(sale)
        self.owed(sale)
        services.cancel_sale(sale, self.manager, "defaulted")
        self.assertEqual(self.figures(), (Decimal("0"), 0, 0))

    def test_another_companys_documents_are_never_counted(self):
        estate = Estate.objects.create(company=self.other, name="Elsewhere")
        plot = Plot.objects.create(company=self.other, estate=estate, plot_number="X-1")
        buyer = Buyer.objects.create(company=self.other, full_name="Other Buyer")
        foreign = services.create_sale(self.other, plot, buyer, 500, self.today, 500, self.today)
        entry = DocumentType.objects.create(company=self.other, name="Foreign Doc")
        SaleDocument.objects.create(company=self.other, sale=foreign, document_type=entry, name_snapshot="x",
                                    state=SaleDocument.State.ISSUED, issued_on=self.today)
        SaleDocument.objects.create(company=self.other, sale=foreign, document_type=DocumentType.objects.create(
            company=self.other, name="Foreign Owed"), name_snapshot="y", state=SaleDocument.State.NOT_APPLICABLE,
            not_applicable_reason="none")
        self.assertEqual(self.figures(), (Decimal("0"), 0, 0))
        self.assertEqual(list(services.exposure_sales(self.co)), [])

    # ---- the fully-paid variant, the helper itself and the estate breakdown ----

    def test_the_fully_paid_variant_follows_the_same_rule(self):
        part = self.sale(price=1000, paid=400)
        self.finish_work(part)
        self.owed(part, obligation=part.obligations.get())                       # partly paid, owed document
        full = self.sale(price=1000, paid=1000)
        self.finish_work(full)
        self.owed(full)
        self.assertEqual(services.exposure(self.co)["amount"], Decimal("1400.00"))
        self.assertEqual(services.exposure(self.co, fully_paid_only=True)["amount"], Decimal("1000.00"))

    def test_without_documents_the_helper_returns_exactly_what_the_old_rule_did(self):
        self.sale(price=1000, paid=1000)
        self.sale(price=2000, paid=500, sold_ago=10)
        done = self.sale(price=300, paid=300)
        self.finish_work(done)
        cancelled = self.sale(price=900, paid=900)
        services.cancel_sale(cancelled, self.manager, "defaulted")
        old_rule = set(Sale.objects.filter(
            company=self.co, status=Sale.Status.ACTIVE, id__in=services.open_obligations(self.co).values("sale_id")
        ).values_list("pk", flat=True))
        self.assertEqual(set(services.exposure_sales(self.co).values_list("pk", flat=True)), old_rule)
        self.assertEqual(len(old_rule), 2)

    def test_by_estate_money_includes_owed_documents_and_still_adds_up_to_the_total(self):
        ready = self.sale(self.e1, price=1000, paid=1000)
        self.finish_work(ready)
        self.owed(ready)
        self.sale(self.e2, price=2000, paid=500, sold_ago=10)
        rows = reports.by_estate(self.co, self.today)
        by_name = {r["estate"]: r for r in rows}
        self.assertEqual(by_name["Green Valley"]["amount"], Decimal("1000.00"))
        self.assertEqual((by_name["Green Valley"]["sales"], by_name["Green Valley"]["open_obligations"]), (1, 0))
        self.assertEqual(sum(r["amount"] for r in rows), services.exposure(self.co)["amount"])

    def test_operational_figures_do_not_change_when_a_document_is_owed(self):
        sale = self.sale(price=1000, paid=1000)
        self.finish_work(sale)

        def operational():
            return (reports.attention_summary(self.co, self.today), services.ageing(self.co, self.today),
                    services.open_obligations(self.co).count(), reports.average_wait_days(self.co, self.today),
                    reports.workload(self.co, self.today)["by_department"])

        before = operational()
        self.owed(sale)
        self.assertEqual(operational(), before)
