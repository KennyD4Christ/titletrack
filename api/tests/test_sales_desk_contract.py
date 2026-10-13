import json
from pathlib import Path

from django.conf import settings
from django.urls import reverse

from api.tests.test_sales_desk import DeskBase

FIXTURES = Path(settings.BASE_DIR) / "frontend" / "src" / "test" / "fixtures"
PAYMENT_KEYS = {"id", "kind", "amount", "paid_on", "reference", "note", "recorded_by_name", "created_at", "reverses"}
CREATED_KEYS = {"id", "type_name", "started_on", "due_date"}


def key_tree(value):
    """The field names of a response, nested, ignoring the values (and the order of fields)."""
    if isinstance(value, dict):
        return {key: key_tree(item) for key, item in value.items()}
    if isinstance(value, list):
        return [key_tree(value[0])] if value else []
    return None


class FrontendFixtureContractTests(DeskBase):
    """The React tests run on saved responses. These tests fail if a saved response stops having the same
    fields as the real API, so the two can never drift apart without somebody noticing."""

    def fixture(self, name):
        path = FIXTURES / name
        if not path.exists():
            self.skipTest("frontend fixtures not present")
        return json.loads(path.read_text())

    def test_the_list_fixture_has_the_real_fields(self):
        real = self.get(self.manager, "sales-desk-list").data
        saved = self.fixture("sales_list.json")
        self.assertEqual(set(real), set(saved))
        self.assertTrue(real["results"] and saved["results"])
        self.assertEqual(key_tree(real["results"][0]), key_tree(saved["results"][0]))

    def test_the_detail_fixture_has_the_real_fields_in_every_nested_list(self):
        real = self.detail(self.s2)
        saved = self.fixture("sale_detail.json")
        for part in ("obligations", "payments", "history"):
            self.assertTrue(real[part] and saved[part], part)
        self.assertTrue(real["history"][0]["changes_display"] and saved["history"][0]["changes_display"])
        self.assertEqual(key_tree(real), key_tree(saved))

    def test_the_buyer_search_fixture_has_the_real_fields(self):
        real = self.get(self.manager, "sales-desk-buyer-search", params={"q": "bayo"}).data
        saved = self.fixture("buyer_search.json")
        self.assertTrue(real["results"] and saved["results"])
        self.assertEqual(key_tree(real), key_tree(saved))

    def test_the_payment_and_reversal_responses_have_the_fields_the_screens_read(self):
        detail_keys = set(self.detail(self.s2))
        paid = self.pay(self.s2, "600")
        self.assertEqual(paid.status_code, 201, paid.data)
        self.assertEqual(set(paid.data), detail_keys | {"payment", "obligations_created", "duplicate"})
        self.assertEqual(set(paid.data["payment"]), PAYMENT_KEYS)
        self.assertEqual(set(paid.data["obligations_created"][0]), CREATED_KEYS)
        self.client.force_authenticate(self.manager)
        url = reverse("sales-desk-reverse-payment", kwargs={"pk": self.s2.pk, "payment_id": paid.data["payment"]["id"]})
        reversed_ = self.client.post(url, {"reason": "Entered twice"}, format="json")
        self.assertEqual(reversed_.status_code, 200, reversed_.data)
        self.assertEqual(set(reversed_.data), detail_keys | {"reversal"})
        self.assertEqual(set(reversed_.data["reversal"]), PAYMENT_KEYS)
        reinstated = self.post(self.manager, "sales-desk-cancel", self.s2.pk, {"reason": "defaulted"})
        self.assertEqual(reinstated.status_code, 200)
        again = self.post(self.manager, "sales-desk-reinstate", self.s2.pk, {})
        self.assertEqual(set(again.data), detail_keys | {"obligations_created"})

    def test_the_occupied_plot_response_has_the_fields_the_form_reads(self):
        payload = self.new_sale_payload(estate=self.estate.pk, estate_name="", plot_number="A-2", buyer_name="Someone Else")
        r = self.post(self.manager, "sales-desk-list", payload=payload)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(set(r.data), {"detail", "existing_sale"})
        self.assertEqual(set(r.data["existing_sale"]), {"id", "buyer", "buyer_name"})
