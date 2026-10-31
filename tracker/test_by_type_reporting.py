from decimal import Decimal
from django.urls import reverse
from rest_framework.test import APIClient
from . import reports, services
from .models import Buyer, Estate, ObligationType, Plot
from .test_cancel_and_reports import Base

ROW_KEYS = {"type_id", "type", "open", "overdue", "buyers", "amount", "oldest_days"}


class ByTypeDeactivatedTypeTests(Base):
    def deactivate(self, obligation_type):
        ObligationType.objects.filter(pk=obligation_type.pk).update(is_active=False)

    def rows(self, company=None):
        return {r["type"]: r for r in reports.by_type(company or self.co, self.today)}

    def test_active_types_are_reported_as_before(self):
        self.sale(price=1000, paid=1000)
        rows = self.rows()
        self.assertEqual(set(rows), {"Allocation Letter", "Deed"})
        self.assertEqual((rows["Deed"]["open"], rows["Allocation Letter"]["open"]), (1, 1))
        self.assertEqual(set(rows["Deed"]), ROW_KEYS)

    def test_a_deactivated_type_with_open_work_stays_listed_with_correct_figures(self):
        self.sale(price=1000, paid=1000, sold_ago=100)
        self.deactivate(self.deed)
        deed = self.rows()["Deed"]
        self.assertEqual(deed["type_id"], self.deed.pk)
        self.assertEqual((deed["open"], deed["overdue"], deed["buyers"]), (1, 1, 1))
        self.assertEqual(deed["amount"], Decimal("1000.00"))
        self.assertEqual(deed["oldest_days"], 100)

    def test_a_deactivated_type_with_no_open_work_is_still_hidden(self):
        done = self.sale().obligations.get(obligation_type=self.deed)
        not_needed = self.sale().obligations.get(obligation_type=self.deed)
        services.update_obligation(done, self.manager, status="completed")
        services.update_obligation(not_needed, self.manager, status="not_applicable")
        self.deactivate(self.deed)
        self.assertNotIn("Deed", self.rows())

    def test_a_deactivated_type_whose_only_open_work_is_on_a_cancelled_sale_is_hidden(self):
        services.cancel_sale(self.sale(), self.manager, "defaulted")
        self.deactivate(self.deed)
        self.assertNotIn("Deed", self.rows())

    def test_the_breakdown_always_adds_up_to_the_open_total(self):
        self.sale()
        self.sale(sold_ago=10, paid=500)
        self.deactivate(self.deed)
        total = sum(r["open"] for r in reports.by_type(self.co, self.today))
        self.assertEqual(total, reports.attention_summary(self.co, self.today)["open"])
        self.assertEqual(total, services.open_obligations(self.co).count())

    def test_another_companys_types_are_never_listed(self):
        estate = Estate.objects.create(company=self.other, name="Elsewhere")
        plot = Plot.objects.create(company=self.other, estate=estate, plot_number="X-1")
        buyer = Buyer.objects.create(company=self.other, full_name="Other Buyer")
        foreign = ObligationType.objects.create(
            company=self.other, name="Foreign Doc", trigger="on_sale", sla_days=30
        )
        services.create_sale(self.other, plot, buyer, 1000, self.today)
        self.deactivate(foreign)
        self.assertNotIn("Foreign Doc", self.rows())
        self.assertIn("Foreign Doc", self.rows(self.other))

    def test_the_dashboard_endpoint_keeps_its_fields_and_includes_deactivated_types(self):
        self.sale()
        self.deactivate(self.deed)
        client = APIClient()
        client.force_authenticate(self.manager)
        response = client.get(reverse("dashboard-by-type"))
        self.assertEqual(response.status_code, 200)
        rows = {row["type"]: row for row in response.data}
        self.assertIn("Deed", rows)
        self.assertEqual(set(rows["Deed"]), ROW_KEYS)
        self.assertRegex(rows["Deed"]["amount"], r"^\d+\.\d{2}$")
