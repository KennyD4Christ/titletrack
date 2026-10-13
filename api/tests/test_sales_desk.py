from datetime import timedelta

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from api.permissions import MANAGER, MATRIX, MD
from api.tests.test_obligations import ObligationApiBase
from tracker import services
from tracker.models import Buyer, Estate, Obligation, Payment, Plot, Sale, SaleEvent


class DeskBase(ObligationApiBase):
    def post(self, user, name, pk=None, payload=None):
        self.client.force_authenticate(user)
        return self.client.post(reverse(name, args=[pk] if pk is not None else None), payload or {}, format="json")

    def get(self, user, name, pk=None, params=None):
        self.client.force_authenticate(user)
        return self.client.get(reverse(name, args=[pk] if pk is not None else None), params or {})

    def detail(self, sale, user=None):
        r = self.get(user or self.manager, "sales-desk-detail", sale.pk)
        self.assertEqual(r.status_code, 200, r.data)
        return r.data

    def pay(self, sale, amount, key="key-1", user=None, **extra):
        return self.post(user or self.manager, "sales-desk-record-payment", sale.pk,
                         {"amount": amount, "idempotency_key": key, **extra})

    def listing(self, params=None, user=None):
        r = self.get(user or self.manager, "sales-desk-list", params={"page_size": 200, **(params or {})})
        self.assertEqual(r.status_code, 200, r.data)
        return {row["id"] for row in r.data["results"]}, r.data

    def counts(self):
        return tuple(m.objects.count() for m in (Estate, Plot, Buyer, Sale, Payment, SaleEvent, Obligation))

    def new_sale_payload(self, **extra):
        return {"estate_name": "Lakeside Meadows", "plot_number": "N-1", "buyer_name": "Ada Obi", "price": "1000",
                "sale_date": self.today.isoformat(), **extra}


class SalesDeskPermissionTests(DeskBase):
    def test_only_md_and_manager_may_use_the_sales_desk(self):
        pk = self.s2.pk
        gets = [reverse("sales-desk-list"), reverse("sales-desk-detail", args=[pk]), reverse("sales-desk-buyer-search")]
        posts = [reverse("sales-desk-list"), reverse("sales-desk-record-payment", args=[pk]),
                 reverse("sales-desk-reverse-payment", kwargs={"pk": pk, "payment_id": 1}),
                 reverse("sales-desk-edit-buyer", args=[pk]), reverse("sales-desk-cancel", args=[pk]),
                 reverse("sales-desk-reinstate", args=[pk])]
        for user in (self.staff_legal, self.staff_survey, self.sales, self.viewer, self.outsider):
            self.client.force_authenticate(user)
            for url in gets:
                self.assertEqual(self.client.get(url).status_code, 403, f"{user.role} GET {url}")
            for url in posts:
                self.assertEqual(self.client.post(url, {}, format="json").status_code, 403, f"{user.role} POST {url}")
        for user in (self.md, self.manager):
            self.client.force_authenticate(user)
            for url in gets:
                self.assertEqual(self.client.get(url).status_code, 200, f"{user.role} GET {url}")
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(reverse("sales-desk-list")).status_code, (401, 403))

    def test_every_sales_desk_action_is_its_own_matrix_entry_for_md_and_manager(self):
        for action in ("view_sales", "add_sale", "record_payment", "reverse_payment", "edit_buyer"):
            self.assertEqual(MATRIX[action], {MD, MANAGER}, action)

    def test_unsupported_methods_are_refused(self):
        self.client.force_authenticate(self.manager)
        url = reverse("sales-desk-detail", args=[self.s2.pk])
        for method in ("put", "patch", "delete"):
            self.assertEqual(getattr(self.client, method)(url, {}, format="json").status_code, 405, method)

    def test_the_existing_sales_route_is_still_read_only_and_cancel_still_works(self):
        self.client.force_authenticate(self.manager)
        self.assertEqual(self.client.post(reverse("sale-list"), {}, format="json").status_code, 405)
        r = self.post(self.manager, "sale-cancel", self.s3.pk, {"reason": "defaulted"})
        self.assertEqual((r.status_code, r.data["status"]), (200, "cancelled"))


class SalesDeskIsolationTests(DeskBase):
    def test_another_companys_sales_are_invisible_and_untouchable(self):
        ids, _ = self.listing()
        self.assertNotIn(self.b_sale.pk, ids)
        pk = self.b_sale.pk
        self.assertEqual(self.get(self.manager, "sales-desk-detail", pk).status_code, 404)
        before = self.counts()
        for name, payload in (("sales-desk-record-payment", {"amount": "1", "idempotency_key": "k"}),
                              ("sales-desk-edit-buyer", {"full_name": "Hijacked"}),
                              ("sales-desk-cancel", {"reason": "hijack"}), ("sales-desk-reinstate", {})):
            self.assertEqual(self.post(self.manager, name, pk, payload).status_code, 404, name)
        self.client.force_authenticate(self.manager)
        url = reverse("sales-desk-reverse-payment", kwargs={"pk": pk, "payment_id": 1})
        self.assertEqual(self.client.post(url, {"reason": "nope"}, format="json").status_code, 404)
        self.assertEqual(self.counts(), before)
        self.assertEqual(Sale.objects.get(pk=pk).status, "active")
        only_b, _ = self.listing(user=self.b_manager)
        self.assertEqual(only_b, {self.b_sale.pk})

    def test_another_companys_estate_or_buyer_cannot_be_used_when_adding_a_sale(self):
        foreign_estate = self.b_sale.plot.estate_id
        foreign_buyer = self.b_sale.buyer_id
        before = self.counts()
        r = self.post(self.manager, "sales-desk-list", payload=self.new_sale_payload(estate=foreign_estate, estate_name=""))
        self.assertEqual(r.status_code, 400)
        self.assertIn("estate", r.data)
        r = self.post(self.manager, "sales-desk-list", payload=self.new_sale_payload(buyer=foreign_buyer, buyer_name=""))
        self.assertEqual(r.status_code, 400)
        self.assertIn("buyer", r.data)
        self.assertEqual(self.counts(), before)


class SalesDeskListTests(DeskBase):
    def test_rows_show_money_buyer_plot_and_obligation_counts(self):
        ids, data = self.listing()
        self.assertEqual(ids, {self.s1.pk, self.s2.pk, self.s3.pk})
        rows = {r["id"]: r for r in data["results"]}
        partial = rows[self.s2.pk]
        self.assertEqual((partial["buyer_name"], partial["plot_number"], partial["estate_name"]),
                         ("Bayo Lawal", "A-2", "Green Valley"))
        self.assertEqual((partial["price"], partial["amount_paid"], partial["balance_due"], partial["is_fully_paid"]),
                         ("1000.00", "400.00", "600.00", False))
        self.assertEqual((partial["obligations_total"], partial["obligations_open"]), (1, 1))
        full = rows[self.s1.pk]
        self.assertEqual((full["balance_due"], full["is_fully_paid"], full["obligations_total"],
                          full["obligations_open"]), ("0.00", True, 2, 2))

    def test_filters(self):
        self.assertEqual(self.listing({"q": "bayo"})[0], {self.s2.pk})
        self.assertEqual(self.listing({"q": "R-1"})[0], {self.s3.pk})
        self.assertEqual(self.listing({"estate": self.estate2.pk})[0], {self.s3.pk})
        self.assertEqual(self.listing({"fully_paid": "1"})[0], {self.s1.pk, self.s3.pk})
        self.assertEqual(self.listing({"fully_paid": "0"})[0], {self.s2.pk})
        services.cancel_sale(self.s3, self.manager, "defaulted")
        self.assertEqual(self.listing({"status": "cancelled"})[0], {self.s3.pk})
        self.assertEqual(self.listing({"status": "active"})[0], {self.s1.pk, self.s2.pk})
        self.assertEqual(self.listing()[0], {self.s1.pk, self.s2.pk, self.s3.pk})
        self.assertEqual(self.get(self.manager, "sales-desk-list", params={"status": "nonsense"}).status_code, 400)

    def test_the_number_of_queries_does_not_grow_with_the_number_of_sales(self):
        self.client.force_authenticate(self.manager)
        url = reverse("sales-desk-list")
        with CaptureQueriesContext(connection) as small:
            self.client.get(url, {"page_size": 200})
        for i in range(15):
            self.sale(self.estate, f"Y-{i}", f"Buyer {i}", paid=(i % 2 == 0), paid_ago=3, sold_ago=9)
        with CaptureQueriesContext(connection) as large:
            r = self.client.get(url, {"page_size": 200})
        self.assertGreater(r.data["count"], 15)
        self.assertEqual(len(small), len(large))


class SalesDeskDetailTests(DeskBase):
    def test_detail_has_the_summary_obligations_payments_history_and_what_the_screen_may_offer(self):
        data = self.detail(self.s2)
        self.assertEqual(data["buyer_detail"]["full_name"], "Bayo Lawal")
        self.assertEqual([o["type_name"] for o in data["obligations"]], ["Allocation Letter"])
        self.assertEqual([(p["kind"], p["amount"]) for p in data["payments"]], [("opening", "400.00")])
        self.assertEqual([e["action"] for e in data["history"]], ["created"])
        self.assertEqual(data["can"], {"record_payment": True, "reverse_payment": True, "edit_buyer": True,
                                       "cancel": True, "reinstate": False})
        self.assertFalse(self.detail(self.s1)["can"]["record_payment"])       # fully paid: nothing left to record


class AddSaleApiTests(DeskBase):
    def test_adds_a_sale_with_a_new_estate_and_buyer(self):
        before = self.counts()
        payload = self.new_sale_payload(phone="0803 111 2222", email="ada@example.com", price="1500000",
                                        amount_paid="500000")
        r = self.post(self.manager, "sales-desk-list", payload=payload)
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual((r.data["estate_name"], r.data["plot_number"], r.data["buyer_name"]),
                         ("Lakeside Meadows", "N-1", "Ada Obi"))
        self.assertEqual((r.data["price"], r.data["amount_paid"]), ("1500000.00", "500000.00"))
        self.assertEqual([(p["kind"], p["recorded_by_name"]) for p in r.data["payments"]], [("opening", "Mike")])
        self.assertEqual([o["type_name"] for o in r.data["obligations"]], ["Allocation Letter"])
        self.assertEqual((r.data["history"][0]["action"], r.data["history"][0]["actor_name"]), ("created", "Mike"))
        self.assertEqual(Sale.objects.count(), before[3] + 1)
        self.assertEqual(Estate.objects.count(), before[0] + 1)

    def test_adds_a_sale_for_an_existing_estate_and_buyer_without_creating_duplicates(self):
        before = self.counts()
        payload = self.new_sale_payload(estate=self.estate.pk, estate_name="", buyer=self.s2.buyer_id, buyer_name="")
        r = self.post(self.manager, "sales-desk-list", payload=payload)
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["buyer"], self.s2.buyer_id)
        after = self.counts()
        self.assertEqual((after[0], after[2]), (before[0], before[2]))        # no new estate, no new buyer

    def test_a_fully_paid_sale_gets_its_deed_obligation(self):
        r = self.post(self.manager, "sales-desk-list", payload=self.new_sale_payload(amount_paid="1000"))
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual({o["type_name"] for o in r.data["obligations"]}, {"Allocation Letter", "Deed"})

    def test_a_plot_with_an_active_sale_is_refused_and_names_the_current_buyer(self):
        before = self.counts()
        payload = self.new_sale_payload(estate=self.estate.pk, estate_name="", plot_number="a-2",
                                        buyer_name="Someone Else")
        r = self.post(self.manager, "sales-desk-list", payload=payload)
        self.assertEqual(r.status_code, 409)
        self.assertIn("Bayo Lawal", r.data["detail"])
        self.assertEqual((r.data["existing_sale"]["id"], r.data["existing_sale"]["buyer_name"]),
                         (self.s2.pk, "Bayo Lawal"))
        self.assertEqual(self.counts(), before)

    def test_a_cancelled_sale_frees_the_plot(self):
        services.cancel_sale(self.s2, self.manager, "defaulted")
        payload = self.new_sale_payload(estate=self.estate.pk, estate_name="", plot_number="A-2", buyer_name="New Buyer")
        self.assertEqual(self.post(self.manager, "sales-desk-list", payload=payload).status_code, 201)

    def test_bad_input_is_refused_and_nothing_is_written(self):
        before = self.counts()
        cases = {
            "price zero": {"price": "0"}, "price text": {"price": "abc"}, "too many decimals": {"price": "10.505"},
            "paid more than price": {"amount_paid": "1500"}, "future date": {"sale_date": (self.today + timedelta(days=9)).isoformat()},
            "no buyer": {"buyer_name": ""}, "no estate": {"estate_name": ""}, "bad email": {"email": "nope"},
            "no plot": {"plot_number": ""}, "bad date": {"sale_date": "31/02/2026"},
        }
        for label, extra in cases.items():
            r = self.post(self.manager, "sales-desk-list", payload=self.new_sale_payload(**extra))
            self.assertEqual(r.status_code, 400, f"{label}: {r.data}")
        payload = self.new_sale_payload()
        del payload["sale_date"]
        r = self.post(self.manager, "sales-desk-list", payload=payload)
        self.assertEqual(r.status_code, 400)
        self.assertIn("sale_date", r.data)
        self.assertEqual(self.counts(), before)


class RecordPaymentApiTests(DeskBase):
    def test_a_partial_payment_updates_the_totals_and_creates_no_obligation(self):
        r = self.pay(self.s2, "300", reference="TRF-1")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual((r.data["amount_paid"], r.data["balance_due"], r.data["is_fully_paid"]),
                         ("700.00", "300.00", False))
        self.assertEqual((r.data["payment"]["kind"], r.data["payment"]["amount"], r.data["payment"]["recorded_by_name"]),
                         ("payment", "300.00", "Mike"))
        self.assertEqual((r.data["obligations_created"], r.data["duplicate"]), ([], False))
        self.assertEqual([p["kind"] for p in r.data["payments"]], ["opening", "payment"])
        self.assertEqual(r.data["history"][-1]["action"], "payment")

    def test_the_payment_that_completes_the_price_reports_the_obligations_it_created(self):
        r = self.pay(self.s2, "600", paid_on=self.today.isoformat())
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual((r.data["amount_paid"], r.data["is_fully_paid"], r.data["fully_paid_on"]),
                         ("1000.00", True, self.today.isoformat()))
        self.assertEqual([o["type_name"] for o in r.data["obligations_created"]], ["Deed"])
        self.assertFalse(r.data["can"]["record_payment"])
        self.assertEqual(Obligation.objects.filter(sale=self.s2, obligation_type=self.deed).count(), 1)

    def test_a_repeated_request_is_recorded_once_and_creates_nothing_twice(self):
        first = self.pay(self.s2, "600", key="same")
        again = self.pay(self.s2, "600", key="same")
        self.assertEqual((first.status_code, again.status_code), (201, 200))
        self.assertTrue(again.data["duplicate"])
        self.assertEqual(again.data["obligations_created"], [])
        self.assertEqual(again.data["payment"]["id"], first.data["payment"]["id"])
        self.assertEqual(Payment.objects.filter(sale=self.s2, kind="payment").count(), 1)
        self.assertEqual(Obligation.objects.filter(sale=self.s2, obligation_type=self.deed).count(), 1)
        self.assertEqual(Sale.objects.get(pk=self.s2.pk).amount_paid, 1000)

    def test_the_request_key_is_required(self):
        r = self.post(self.manager, "sales-desk-record-payment", self.s2.pk, {"amount": "100"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("idempotency_key", r.data)
        r = self.pay(self.s2, "100", key="")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Payment.objects.filter(sale=self.s2, kind="payment").count(), 0)

    def test_the_same_key_for_a_different_amount_is_refused(self):
        self.pay(self.s2, "100", key="k")
        self.assertEqual(self.pay(self.s2, "200", key="k").status_code, 400)
        self.assertEqual(Payment.objects.filter(sale=self.s2, kind="payment").count(), 1)

    def test_a_payment_above_the_price_is_refused_with_a_useful_message(self):
        r = self.pay(self.s2, "700")
        self.assertEqual(r.status_code, 400)
        self.assertIn("600.00", r.data["detail"])
        self.assertEqual(Sale.objects.get(pk=self.s2.pk).amount_paid, 400)
        self.assertEqual(self.pay(self.s1, "1", key="k2").status_code, 400)         # already fully paid

    def test_bad_amounts_and_dates_are_refused(self):
        for amount in ("0", "-5", "abc", "10.505", "NaN"):
            self.assertEqual(self.pay(self.s2, amount, key=f"k-{amount}").status_code, 400, amount)
        for day in ("31/02/2026", "1980-01-01", (self.today + timedelta(days=9)).isoformat()):
            self.assertEqual(self.pay(self.s2, "10", key=f"d-{day}", paid_on=day).status_code, 400, day)
        self.assertEqual(Payment.objects.filter(sale=self.s2, kind="payment").count(), 0)

    def test_a_cancelled_sale_takes_no_payments_and_a_reinstated_one_does(self):
        self.post(self.manager, "sales-desk-cancel", self.s2.pk, {"reason": "Buyer defaulted"})
        r = self.pay(self.s2, "10")
        self.assertEqual(r.status_code, 409)
        self.assertIn("cancelled", r.data["detail"])
        self.post(self.manager, "sales-desk-reinstate", self.s2.pk, {"note": "mistake"})
        self.assertEqual(self.pay(self.s2, "10", key="k2").status_code, 201)


class ReversePaymentApiTests(DeskBase):
    def reverse(self, sale, payment_id, reason="Cheque bounced", user=None):
        self.client.force_authenticate(user or self.manager)
        url = reverse("sales-desk-reverse-payment", kwargs={"pk": sale.pk, "payment_id": payment_id})
        return self.client.post(url, {"reason": reason} if reason is not None else {}, format="json")

    def test_a_reversal_keeps_its_reason_and_the_ledger_stays_traceable(self):
        paid = self.pay(self.s2, "300").data["payment"]
        r = self.reverse(self.s2, paid["id"])
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["amount_paid"], "400.00")
        self.assertEqual([p["kind"] for p in r.data["payments"]], ["opening", "payment", "reversal"])
        original, reversal = r.data["payments"][1], r.data["payments"][2]
        self.assertTrue(original["is_reversed"])
        self.assertFalse(original["reversible"])
        self.assertEqual((reversal["amount"], reversal["note"], reversal["reverses"], reversal["recorded_by_name"]),
                         ("-300.00", "Cheque bounced", paid["id"], "Mike"))
        self.assertEqual(r.data["history"][-1]["action"], "payment_reversed")
        self.assertEqual(r.data["history"][-1]["note"], "Cheque bounced")

    def test_refusals(self):
        paid = self.pay(self.s2, "300").data["payment"]
        self.assertEqual(self.reverse(self.s2, paid["id"], reason="").status_code, 400)
        self.assertEqual(self.reverse(self.s2, paid["id"], reason=None).status_code, 400)
        self.assertEqual(self.reverse(self.s2, paid["id"]).status_code, 200)
        self.assertEqual(self.reverse(self.s2, paid["id"]).status_code, 409)             # already reversed
        self.assertEqual(self.reverse(self.s1, paid["id"]).status_code, 404)             # another sale's payment
        self.assertEqual(self.reverse(self.s2, 999999).status_code, 404)

    def test_a_cancelled_sale_cannot_have_payments_reversed(self):
        paid = self.pay(self.s2, "300").data["payment"]
        self.post(self.manager, "sales-desk-cancel", self.s2.pk, {"reason": "defaulted"})
        self.assertEqual(self.reverse(self.s2, paid["id"]).status_code, 409)


class EditBuyerApiTests(DeskBase):
    def test_the_shared_buyer_is_edited_without_merging_or_moving_sales(self):
        plot = Plot.objects.create(company=self.a, estate=self.estate, plot_number="Q-1")
        second = services.create_sale(self.a, plot, self.s2.buyer, 500, self.today)       # same person, second plot
        namesake = Buyer.objects.create(company=self.a, full_name=self.s2.buyer.full_name)
        buyers_before = Buyer.objects.count()
        r = self.post(self.manager, "sales-desk-edit-buyer", self.s2.pk,
                      {"full_name": "Bayo Lawal-Smith", "phone": "0803 000 1111"})
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual((r.data["buyer_detail"]["full_name"], r.data["buyer_detail"]["phone"]),
                         ("Bayo Lawal-Smith", "0803 000 1111"))
        self.assertEqual(r.data["buyer"], self.s2.buyer_id)                               # ownership unchanged
        self.assertEqual(r.data["history"][-1]["action"], "buyer_edited")
        edited = SaleEvent.objects.filter(action="buyer_edited")
        self.assertEqual({e.sale_id for e in edited}, {self.s2.pk, second.pk})
        namesake.refresh_from_db()
        self.assertEqual(namesake.full_name, "Bayo Lawal")
        self.assertEqual(Buyer.objects.count(), buyers_before)

    def test_refusals(self):
        for payload in ({}, {"full_name": ""}, {"email": "nope"}, {"full_name": "Bayo Lawal"}):
            self.assertEqual(self.post(self.manager, "sales-desk-edit-buyer", self.s2.pk, payload).status_code, 400,
                             payload)
        self.assertFalse(SaleEvent.objects.filter(action="buyer_edited").exists())


class CancelReinstateApiTests(DeskBase):
    def test_cancel_needs_a_reason_and_freezes_the_sale(self):
        self.assertEqual(self.post(self.manager, "sales-desk-cancel", self.s2.pk, {"reason": ""}).status_code, 400)
        r = self.post(self.manager, "sales-desk-cancel", self.s2.pk, {"reason": "Buyer defaulted"})
        self.assertEqual((r.status_code, r.data["status"], r.data["cancel_reason"]), (200, "cancelled", "Buyer defaulted"))
        self.assertEqual((r.data["can"]["cancel"], r.data["can"]["reinstate"], r.data["can"]["record_payment"]),
                         (False, True, False))
        self.assertEqual(r.data["history"][-1]["action"], "cancelled")
        self.assertEqual(self.post(self.manager, "sales-desk-cancel", self.s2.pk, {"reason": "again"}).status_code, 409)

    def test_reinstating_reports_the_obligations_it_had_to_create(self):
        Obligation.objects.filter(sale=self.s1, obligation_type=self.deed).delete()
        self.post(self.manager, "sales-desk-cancel", self.s1.pk, {"reason": "defaulted"})
        r = self.post(self.manager, "sales-desk-reinstate", self.s1.pk, {"note": "mistake"})
        self.assertEqual((r.status_code, r.data["status"]), (200, "active"))
        self.assertEqual([o["type_name"] for o in r.data["obligations_created"]], ["Deed"])
        self.assertEqual(r.data["history"][-1]["action"], "reinstated")

    def test_reinstating_is_refused_when_the_plot_was_sold_again(self):
        self.post(self.manager, "sales-desk-cancel", self.s2.pk, {"reason": "defaulted"})
        services.create_sale(self.a, self.s2.plot, Buyer.objects.create(company=self.a, full_name="New"), 900, self.today)
        self.assertEqual(self.post(self.manager, "sales-desk-reinstate", self.s2.pk, {}).status_code, 409)
        self.assertFalse(self.detail(self.s2)["can"]["reinstate"])


class BuyerSearchApiTests(DeskBase):
    def test_finds_buyers_by_name_phone_or_email_within_the_company(self):
        zed = Buyer.objects.create(company=self.a, full_name="Zed Test", phone="0803 555 0000", email="zed@example.com")
        self.client.force_authenticate(self.manager)
        url = reverse("sales-desk-buyer-search")
        for q in ("zed t", "555", "zed@"):
            rows = self.client.get(url, {"q": q}).data["results"]
            self.assertEqual([r["id"] for r in rows], [zed.pk], q)
        self.assertEqual(self.client.get(url, {"q": "z"}).data["results"], [])               # too short
        rows = self.client.get(url, {"q": "bayo"}).data["results"]
        self.assertEqual((rows[0]["id"], rows[0]["sales_count"]), (self.s2.buyer_id, 1))
        self.assertEqual(self.client.get(url, {"q": "Other Buyer"}).data["results"], [])   # another company's buyer
