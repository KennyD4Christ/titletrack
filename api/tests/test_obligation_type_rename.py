from django.urls import reverse
from api.tests.test_obligations import ObligationApiBase
from tracker import services
from tracker.models import Buyer, Obligation, ObligationType, Plot


class ObligationTypeRenameTests(ObligationApiBase):
    def used_sale(self):
        plot = Plot.objects.create(company=self.a, estate=self.estate, plot_number="REN-1")
        buyer = Buyer.objects.create(company=self.a, full_name="Rename Buyer")
        sale = services.create_sale(self.a, plot, buyer, 100, self.today, 100, self.today)
        self.assertTrue(Obligation.objects.filter(obligation_type=self.deed).exists())
        return sale

    def patch(self, obligation_type, payload, user=None):
        self.client.force_authenticate(user or self.manager)
        return self.client.patch(
            self.url("obligation-type-detail", obligation_type.pk), payload, format="json"
        )

    def test_an_unused_type_can_still_be_renamed(self):
        unused = ObligationType.objects.create(
            company=self.a, name="Unused Doc", trigger="on_sale", sla_days=30
        )
        self.assertEqual(self.patch(unused, {"name": "Unused Doc Renamed"}).status_code, 200)
        unused.refresh_from_db()
        self.assertEqual(unused.name, "Unused Doc Renamed")

    def test_a_used_type_cannot_be_renamed_and_history_keeps_its_name(self):
        sale = self.used_sale()
        response = self.patch(self.deed, {"name": "Deed of Assignment"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("name", response.data)
        self.deed.refresh_from_db()
        self.assertEqual(self.deed.name, "Deed")
        self.client.force_authenticate(self.manager)
        listing = self.client.get(self.url("obligation-list"), {"page_size": 200})
        names = {row["type_name"] for row in listing.data["results"]}
        self.assertIn("Deed", names)
        self.assertNotIn("Deed of Assignment", names)
        detail = self.client.get(reverse("sales-desk-detail", args=[sale.pk]))
        self.assertIn("Deed", [o["type_name"] for o in detail.data["obligations"]])

    def test_a_case_only_rename_of_a_used_type_is_refused(self):
        self.used_sale()
        self.assertEqual(self.patch(self.deed, {"name": "DEED"}).status_code, 400)
        self.deed.refresh_from_db()
        self.assertEqual(self.deed.name, "Deed")

    def test_the_other_settings_of_a_used_type_can_still_be_edited(self):
        self.used_sale()
        for payload in (
            {"sla_days": 10},
            {"trigger": "on_sale"},
            {"default_department": self.survey.pk},
            {"is_active": False},
        ):
            self.assertEqual(self.patch(self.deed, payload).status_code, 200, payload)

    def test_resending_the_same_name_is_not_a_rename(self):
        self.used_sale()
        self.assertEqual(self.patch(self.deed, {"name": "Deed"}).status_code, 200)
        self.assertEqual(self.patch(self.deed, {"name": "  Deed  "}).status_code, 200)
        self.client.force_authenticate(self.manager)
        full = {
            "name": "Deed",
            "trigger": self.deed.trigger,
            "sla_days": self.deed.sla_days,
            "default_department": self.deed.default_department_id,
            "is_active": True,
        }
        self.assertEqual(
            self.client.put(
                self.url("obligation-type-detail", self.deed.pk), full, format="json"
            ).status_code,
            200,
        )

    def test_permissions_and_company_isolation_are_unchanged(self):
        self.used_sale()
        for user in (self.staff_legal, self.sales, self.viewer):
            self.assertEqual(self.patch(self.deed, {"name": "Nope"}, user=user).status_code, 403, user.role)
        other = ObligationType.objects.create(
            company=self.b,
            name="Foreign Document",
            trigger="on_sale",
            sla_days=30,
        )
        self.assertEqual(self.patch(other, {"name": "Hijack"}).status_code, 404)
