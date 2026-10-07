import re
from datetime import timedelta

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from api.tests.test_obligations import ObligationApiBase
from tracker import services
from tracker.models import Obligation, Sale

FULL = ("md", "manager", "viewer")


class DashboardBase(ObligationApiBase):
    def setUp(self):
        super().setUp()
        # a bit more variety: s3's deed gets an owner and a reason; s2 gets a reason
        services.update_obligation(self.deed3, self.manager, owner=self.staff_legal, status="in_progress",
                                   reason="Awaiting review", next_action="Review")
        services.update_obligation(self.s2.obligations.get(obligation_type=self.alloc), self.manager,
                                   reason="awaiting   REVIEW")

    def get(self, name, user=None, **params):
        self.client.force_authenticate(user or self.manager)
        return self.client.get(reverse(name), params)

    def home(self, user=None):
        r = self.get("dashboard-home", user)
        self.assertEqual(r.status_code, 200, r.data)
        return r.data

    def list_count(self, **params):
        self.client.force_authenticate(self.manager)
        return self.client.get(reverse("obligation-list"), {"page_size": 1, **params}).data["count"]


class DashboardAccessTests(DashboardBase):
    def test_company_wide_blocks_are_for_md_manager_viewer_only(self):
        names = ["dashboard-attention", "dashboard-exposure", "dashboard-ageing", "dashboard-by-estate", "dashboard-by-type",
                 "dashboard-delay-reasons", "dashboard-oldest", "dashboard-workload"]
        for user, allowed in ((self.md, True), (self.manager, True), (self.viewer, True),
                              (self.staff_legal, False), (self.sales, False)):
            for name in names:
                self.assertEqual(self.get(name, user).status_code, 200 if allowed else 403, f"{user.role} {name}")

    def test_outsider_and_anonymous_are_denied(self):
        self.assertEqual(self.get("dashboard-home", self.outsider).status_code, 403)
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(reverse("dashboard-home")).status_code, (401, 403))

    def test_home_adapts_to_the_role(self):
        for role, user in (("md", self.md), ("manager", self.manager), ("viewer", self.viewer)):
            data = self.home(user)
            self.assertEqual(data["role"], role)
            for key in ("attention", "exposure", "ageing", "by_estate", "by_type", "delay_reasons", "oldest", "workload",
                        "cancelled_last_30_days"):
                self.assertIn(key, data, f"{role} {key}")
        staff = self.home(self.staff_legal)
        self.assertEqual(staff["role"], "staff")
        self.assertIn("queue", staff)
        self.assertNotIn("exposure", staff)                                          # money figures are not for staff
        sales = self.home(self.sales)
        self.assertEqual(set(sales), {"role", "as_of"})

    def test_other_company_data_never_appears(self):
        data = self.home(self.b_manager)
        self.assertEqual([r["estate"] for r in data["by_estate"]], ["B Estate"])
        self.assertEqual(data["exposure"]["buyers"], 1)                              # B's own single buyer only
        mine = self.home(self.manager)
        self.assertNotIn("B Estate", [r["estate"] for r in mine["by_estate"]])
        self.assertEqual({r["department"] for r in mine["workload"]["by_department"]} & {"B Legal"}, set())


class NumbersMatchTheListsTests(DashboardBase):
    """Every number on the dashboard is a link into the list: the counts must agree exactly."""

    def test_attention_counts_equal_list_counts(self):
        self.sale(self.estate, "DS-1", "Due Soon", paid=False, sold_ago=25)           # allocation letter due in 5 days
        attention = self.home()["attention"]
        self.assertGreaterEqual(attention["due_soon"], 1)
        self.assertEqual(attention["due_soon"], self.list_count(due_within="7"))
        self.assertEqual(attention["open"], self.list_count(open="1"))
        self.assertEqual(attention["overdue"], self.list_count(overdue="1"))
        self.assertEqual(attention["stale"], self.list_count(stale="1"))
        self.assertEqual(attention["incomplete"], self.list_count(incomplete="1"))
        self.assertEqual(attention["awaiting_external"], self.list_count(status="awaiting_external"))
        self.assertGreater(attention["overdue"], 0)                                  # fixtures exercise every figure
        self.assertGreater(attention["stale"], 0)
        self.assertGreater(attention["incomplete"], 0)
        self.assertGreater(attention["awaiting_external"], 0)

    def test_ageing_buckets_add_up_to_open(self):
        data = self.home()
        self.assertEqual(sum(data["ageing"].values()), data["attention"]["open"])

    def test_each_ageing_bucket_equals_its_list(self):
        ageing = self.home()["ageing"]
        self.assertEqual(ageing["d0_30"], self.list_count(age_max="30"))
        self.assertEqual(ageing["d31_60"], self.list_count(age_min="31", age_max="60"))
        self.assertEqual(ageing["d61_90"], self.list_count(age_min="61", age_max="90"))
        self.assertEqual(ageing["d90_plus"], self.list_count(age_min="91"))
        self.assertGreater(ageing["d90_plus"], 0)

    def test_estate_rows_add_up_to_the_exposure_total(self):
        data = self.home()
        total = sum(float(r["amount"]) for r in data["by_estate"])
        self.assertEqual(f"{total:.2f}", data["exposure"]["amount"])
        for row in data["by_estate"]:
            self.assertEqual(row["open_obligations"], self.list_count(estate=row["estate_id"], open="1"))
            self.assertEqual(row["overdue"], self.list_count(estate=row["estate_id"], overdue="1"))

    def test_type_rows_match_the_list(self):
        for row in self.home()["by_type"]:
            self.assertEqual(row["open"], self.list_count(type=row["type_id"], open="1"), row["type"])
            self.assertEqual(row["overdue"], self.list_count(type=row["type_id"], overdue="1"), row["type"])

    def test_workload_rows_match_the_list(self):
        for row in self.home()["workload"]["by_owner"]:
            self.assertEqual(row["open"], self.list_count(owner=row["owner_id"], open="1"), row["owner"])
        unowned = sum(r["unowned"] for r in self.home()["workload"]["by_department"])
        self.assertEqual(unowned, self.list_count(open="1") - sum(r["open"] for r in self.home()["workload"]["by_owner"]))

    def test_money_is_formatted_like_the_rest_of_the_api(self):
        data = self.home()
        for value in (data["exposure"]["amount"], data["exposure"]["fully_paid_amount"],
                      *[r["amount"] for r in data["by_estate"]], *[r["amount"] for r in data["by_type"]]):
            self.assertRegex(value, r"^\d+\.\d{2}$")

    def test_delay_reasons_group_case_and_spacing(self):
        reasons = {r["reason"].casefold(): r["count"] for r in self.home()["delay_reasons"]}
        self.assertEqual(reasons["awaiting review"], 2)                              # "Awaiting review" and "awaiting   REVIEW"

    def test_oldest_list_is_ordered_and_limit_is_clamped(self):
        rows = self.get("dashboard-oldest", limit="2").data
        self.assertEqual(len(rows), 2)
        self.assertEqual([r["started_on"] for r in rows], sorted(r["started_on"] for r in rows))
        self.assertEqual(len(self.get("dashboard-oldest", limit="999").data), Obligation.objects.filter(company=self.a).count())
        self.assertEqual(len(self.get("dashboard-oldest", limit="junk").data), 5)    # junk -> default 10; only 5 obligations exist

    def test_dashboard_query_count_does_not_grow_with_data(self):
        self.client.force_authenticate(self.manager)
        with CaptureQueriesContext(connection) as small:
            self.client.get(reverse("dashboard-home"))
        for i in range(25):
            self.sale(self.estate, f"Z-{i}", f"Buyer {i}", paid=True, paid_ago=3, sold_ago=9)
        with CaptureQueriesContext(connection) as large:
            self.client.get(reverse("dashboard-home"))
        self.assertEqual(len(small), len(large))
        self.assertLess(len(large), 40)


class CancelledSaleApiTests(DashboardBase):
    def cancel(self, user, sale, reason="Buyer defaulted"):
        self.client.force_authenticate(user)
        return self.client.post(reverse("sale-cancel", args=[sale.pk]), {"reason": reason}, format="json")

    def test_cancelling_removes_the_sale_from_every_dashboard_number_and_work_list(self):
        before = self.home()
        before_open = self.list_count(open="1")
        r = self.cancel(self.manager, self.s1)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["status"], "cancelled")
        self.assertEqual(r.data["cancel_reason"], "Buyer defaulted")
        after = self.home()
        s1_open = 2                                                                  # allocation letter + deed
        self.assertEqual(after["attention"]["open"], before["attention"]["open"] - s1_open)
        self.assertEqual(self.list_count(open="1"), before_open - s1_open)
        self.assertLess(after["attention"]["overdue"], before["attention"]["overdue"])
        self.assertLess(float(after["exposure"]["amount"]), float(before["exposure"]["amount"]))
        self.assertEqual(after["cancelled_last_30_days"], before["cancelled_last_30_days"] + 1)
        self.assertEqual(after["attention"]["open"], self.list_count(open="1"))      # dashboard and list still agree
        self.assertEqual(self.list_count(), Obligation.objects.filter(company=self.a, sale__status="active").count())

    def test_cancelled_obligations_stay_available_for_audit_but_are_frozen(self):
        self.cancel(self.manager, self.s1)
        self.client.force_authenticate(self.manager)
        listing = self.client.get(reverse("obligation-list"), {"include_cancelled": "1", "page_size": 200})
        rows = {r["id"]: r for r in listing.data["results"]}
        row = rows[self.deed1.pk]
        self.assertEqual((row["sale_status"], row["is_cancelled"], row["is_open"]), ("cancelled", True, False))
        self.assertEqual((row["days_outstanding"], row["days_overdue"], row["staleness"], row["is_incomplete"]), (None, 0, None, False))
        self.assertEqual(row["status"], "awaiting_external")                         # the status was NOT overwritten
        detail = self.client.get(reverse("obligation-detail", args=[self.deed1.pk]))
        self.assertEqual(detail.status_code, 200)                                    # history links keep working
        r = self.client.patch(reverse("obligation-detail", args=[self.deed1.pk]), {"note": "x"}, format="json")
        self.assertEqual(r.status_code, 409)
        events = self.client.get(reverse("obligation-events", args=[self.deed1.pk])).data
        self.assertEqual(events[-1]["note"], "Sale cancelled: Buyer defaulted")
        hidden = self.client.get(reverse("obligation-list"), {"page_size": 200}).data["results"]
        self.assertNotIn(self.deed1.pk, {r["id"] for r in hidden})                   # not in the default work list
        self.assertEqual(self.client.get(reverse("obligation-list"), {"open": "1", "include_cancelled": "1",
                                                                       "page_size": 200}).data["count"],
                         self.list_count(open="1"))                                  # even with the flag, "open" excludes them

    def test_who_can_cancel_and_reinstate(self):
        for user in (self.staff_legal, self.sales, self.viewer):
            self.assertEqual(self.cancel(user, self.s2).status_code, 403, user.role)
        self.assertEqual(self.cancel(self.md, self.s2).status_code, 200)
        self.client.force_authenticate(self.staff_legal)
        self.assertEqual(self.client.post(reverse("sale-reinstate", args=[self.s2.pk]), {}, format="json").status_code, 403)
        self.client.force_authenticate(self.md)
        r = self.client.post(reverse("sale-reinstate", args=[self.s2.pk]), {"note": "mistake"}, format="json")
        self.assertEqual((r.status_code, r.data["status"]), (200, "active"))

    def test_validation_and_state_errors(self):
        self.assertEqual(self.cancel(self.manager, self.s2, reason="").status_code, 400)
        self.assertEqual(self.cancel(self.manager, self.s2, reason="okay").status_code, 200)
        self.assertEqual(self.cancel(self.manager, self.s2, reason="again").status_code, 409)
        self.client.force_authenticate(self.manager)
        self.assertEqual(self.client.post(reverse("sale-reinstate", args=[self.s3.pk]), {}, format="json").status_code, 409)

    def test_other_company_sale_is_404(self):
        self.assertEqual(self.cancel(self.manager, self.b_sale).status_code, 404)
        self.assertEqual(Sale.objects.get(pk=self.b_sale.pk).status, "active")


class MyQueueTests(DashboardBase):
    def test_staff_sees_only_their_own_grouped_work(self):
        self.client.force_authenticate(self.staff_legal)
        data = self.client.get(reverse("my-queue")).data
        ids = {r["id"] for key in ("overdue", "due_soon", "awaiting_external", "other") for r in data[key]}
        self.assertEqual(ids, set(Obligation.objects.filter(owner=self.staff_legal).values_list("id", flat=True)))
        self.assertEqual(data["counts"]["open"], len(ids))
        self.assertIn(self.deed1.pk, {r["id"] for r in data["awaiting_external"]} | {r["id"] for r in data["overdue"]})
        self.assertEqual(self.client.get(reverse("dashboard-home")).data["queue"]["open"], len(ids))

    def test_only_people_who_can_own_work_have_a_queue(self):
        for user, code in ((self.md, 200), (self.manager, 200), (self.staff_legal, 200), (self.staff_survey, 200),
                           (self.sales, 403), (self.viewer, 403), (self.outsider, 403)):
            self.client.force_authenticate(user)
            self.assertEqual(self.client.get(reverse("my-queue")).status_code, code, user.role)
        self.client.force_authenticate(self.staff_survey)
        self.assertEqual(self.client.get(reverse("my-queue")).data["counts"]["open"], 0)

    def test_cancelled_work_leaves_the_queue(self):
        self.client.force_authenticate(self.manager)
        self.client.post(reverse("sale-cancel", args=[self.s1.pk]), {"reason": "defaulted"}, format="json")
        self.client.force_authenticate(self.staff_legal)
        data = self.client.get(reverse("my-queue")).data
        self.assertNotIn(self.deed1.pk, {r["id"] for key in ("overdue", "due_soon", "awaiting_external", "other") for r in data[key]})
