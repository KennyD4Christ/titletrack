from datetime import timedelta

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from tracker import services
from tracker.models import (
    Buyer, Company, Department, Estate, Obligation, ObligationEvent, ObligationType, Plot, User,
)


class ObligationApiBase(APITestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.a = Company.objects.create(name="A Homes")
        self.b = Company.objects.create(name="B Homes")
        self.legal = Department.objects.create(company=self.a, name="Legal")
        self.survey = Department.objects.create(company=self.a, name="Survey")
        self.b_legal = Department.objects.create(company=self.b, name="Legal")

        def user(name, role, dept=None, company=None):
            return User.objects.create_user(name, password="x", first_name=name.title(),
                                            company=company or self.a, role=role, department=dept)

        self.md = user("mary", "md")
        self.manager = user("mike", "manager")
        self.staff_legal = user("sarah", "staff", self.legal)
        self.staff_survey = user("sam", "staff", self.survey)
        self.sales = user("sola", "sales")
        self.viewer = user("vera", "viewer")
        self.outsider = User.objects.create_user("lonely", password="x", role="md")
        self.b_manager = user("bola", "manager", self.b_legal, company=self.b)

        self.alloc = ObligationType.objects.create(company=self.a, name="Allocation Letter", trigger="on_sale", sla_days=30)
        self.deed = ObligationType.objects.create(company=self.a, name="Deed", trigger="on_full_payment", sla_days=90,
                                                  default_department=self.legal)
        ObligationType.objects.create(company=self.b, name="Deed", trigger="on_full_payment", sla_days=90)

        self.estate = Estate.objects.create(company=self.a, name="Green Valley")
        self.estate2 = Estate.objects.create(company=self.a, name="Royal Gardens")
        self.s1 = self.sale(self.estate, "A-1", "Ada Obi", paid=True, paid_ago=100, sold_ago=150)      # old, overdue
        self.s2 = self.sale(self.estate, "A-2", "Bayo Lawal", paid=False, sold_ago=10)                 # partial
        self.s3 = self.sale(self.estate2, "R-1", "Chidi Eze", paid=True, paid_ago=5, sold_ago=20)      # recent

        self.deed1 = self.s1.obligations.get(obligation_type=self.deed)    # started 100d ago, due 10d ago
        self.deed3 = self.s3.obligations.get(obligation_type=self.deed)    # started 5d ago, unassigned
        self.alloc1 = self.s1.obligations.get(obligation_type=self.alloc)
        # deed1 is owned, has a next action, and was last touched 20 days ago -> stale, not incomplete
        services.update_obligation(self.deed1, self.staff_legal, owner=self.staff_legal, status="awaiting_external",
                                   external_party="Adeyemi & Co", next_action="Chase solicitor")
        Obligation.objects.filter(pk=self.deed1.pk).update(last_updated_at=timezone.now() - timedelta(days=20))

        b_estate = Estate.objects.create(company=self.b, name="B Estate")
        b_plot = Plot.objects.create(company=self.b, estate=b_estate, plot_number="B-1")
        b_buyer = Buyer.objects.create(company=self.b, full_name="Other Buyer")
        self.b_sale = services.create_sale(self.b, b_plot, b_buyer, 100, self.today, 100, self.today)
        self.b_obligation = self.b_sale.obligations.first()

    def sale(self, estate, plot_no, buyer_name, paid, sold_ago, paid_ago=None):
        plot = Plot.objects.create(company=self.a, estate=estate, plot_number=plot_no)
        buyer = Buyer.objects.create(company=self.a, full_name=buyer_name)
        sale_date = self.today - timedelta(days=sold_ago)
        if paid:
            return services.create_sale(self.a, plot, buyer, 1000, sale_date, 1000, self.today - timedelta(days=paid_ago))
        return services.create_sale(self.a, plot, buyer, 1000, sale_date, 400)

    def url(self, name, pk=None):
        return reverse(name, args=[pk] if pk else None)

    def ids(self, params=None, as_user=None):
        self.client.force_authenticate(as_user or self.manager)
        r = self.client.get(self.url("obligation-list"), {"page_size": 200, **(params or {})})
        self.assertEqual(r.status_code, 200, r.data)
        return {row["id"] for row in r.data["results"]}


class ObligationReadTests(ObligationApiBase):
    def test_every_role_can_list_and_read_detail(self):
        for u in (self.md, self.manager, self.staff_legal, self.staff_survey, self.sales, self.viewer):
            self.client.force_authenticate(u)
            self.assertEqual(self.client.get(self.url("obligation-list")).status_code, 200, u.role)
            self.assertEqual(self.client.get(self.url("obligation-detail", self.deed1.pk)).status_code, 200, u.role)

    def test_user_without_company_denied(self):
        self.client.force_authenticate(self.outsider)
        self.assertEqual(self.client.get(self.url("obligation-list")).status_code, 403)

    def test_only_own_company_rows_and_other_company_detail_is_404(self):
        ids = self.ids()
        self.assertNotIn(self.b_obligation.pk, ids)
        self.assertEqual(ids, set(Obligation.objects.filter(company=self.a).values_list("id", flat=True)))
        self.client.force_authenticate(self.manager)
        self.assertEqual(self.client.get(self.url("obligation-detail", self.b_obligation.pk)).status_code, 404)

    def test_computed_fields(self):
        self.client.force_authenticate(self.manager)
        row = self.client.get(self.url("obligation-detail", self.deed1.pk)).data
        self.assertEqual(row["days_outstanding"], 100)
        self.assertEqual(row["days_overdue"], 10)
        self.assertEqual(row["staleness"], "red")
        self.assertFalse(row["is_incomplete"])
        self.assertEqual(row["buyer_name"], "Ada Obi")
        self.assertEqual(row["owner_name"], "Sarah")
        self.assertEqual(row["timeline"], {"sla_days": 90, "expected_days": 90, "actual_days": 100, "delay_days": 10})
        self.assertEqual(row["sale_summary"]["amount_paid"], "1000.00")
        unassigned = self.client.get(self.url("obligation-detail", self.deed3.pk)).data
        self.assertTrue(unassigned["is_incomplete"])
        self.assertEqual(unassigned["days_overdue"], 0)
        self.assertEqual(unassigned["staleness"], "green")
        self.assertIsNone(unassigned["owner_name"])

    def test_completed_obligation_has_no_open_metrics(self):
        services.update_obligation(self.deed3, self.manager, status="completed")
        self.client.force_authenticate(self.manager)
        row = self.client.get(self.url("obligation-detail", self.deed3.pk)).data
        for key in ("days_outstanding", "staleness"):
            self.assertIsNone(row[key], key)
        self.assertFalse(row["is_open"])
        self.assertFalse(row["is_incomplete"])
        self.assertEqual(row["days_overdue"], 0)

    def test_filters_match_the_service_layer_definitions(self):
        co = self.a
        self.assertEqual(self.ids({"overdue": "1"}), set(services.overdue(co).values_list("id", flat=True)))
        self.assertEqual(self.ids({"stale": "1"}), set(services.stale(co).values_list("id", flat=True)))
        open_ids = set(services.open_obligations(co).values_list("id", flat=True))
        self.assertEqual(self.ids({"open": "true"}), open_ids)
        all_ids = self.ids()
        self.assertEqual(self.ids({"overdue": "0"}), all_ids - self.ids({"overdue": "1"}))
        self.assertTrue(self.ids({"overdue": "1"}))      # fixtures really contain overdue rows
        self.assertTrue(self.ids({"stale": "1"}))

    def test_incomplete_filter(self):
        incomplete = self.ids({"incomplete": "1"})
        self.assertIn(self.deed3.pk, incomplete)         # no owner
        self.assertNotIn(self.deed1.pk, incomplete)      # owner + next action
        services.update_obligation(self.deed3, self.manager, owner=self.staff_legal, next_action="   ")
        self.assertIn(self.deed3.pk, self.ids({"incomplete": "1"}))     # whitespace-only next action still incomplete
        services.update_obligation(self.deed3, self.manager, next_action="Prepare deed")
        self.assertNotIn(self.deed3.pk, self.ids({"incomplete": "1"}))

    def test_other_filters(self):
        self.assertEqual(self.ids({"status": "awaiting_external"}), {self.deed1.pk})
        self.assertEqual(self.ids({"status": "awaiting_external,not_started"}),
                         set(Obligation.objects.filter(company=self.a).values_list("id", flat=True)))
        self.assertEqual(self.ids({"owner": self.staff_legal.pk}), {self.deed1.pk})
        self.assertEqual(self.ids({"type": self.deed.pk}), set(Obligation.objects.filter(obligation_type=self.deed).values_list("id", flat=True)))
        self.assertEqual(self.ids({"estate": self.estate2.pk}), set(self.s3.obligations.values_list("id", flat=True)))
        self.assertEqual(self.ids({"department": self.legal.pk}),
                         set(Obligation.objects.filter(department=self.legal).values_list("id", flat=True)))
        self.assertEqual(self.ids({"external_party": "adeyemi"}), {self.deed1.pk})
        self.assertEqual(self.ids({"q": "bayo"}), set(self.s2.obligations.values_list("id", flat=True)))
        self.assertEqual(self.ids({"q": "R-1"}), set(self.s3.obligations.values_list("id", flat=True)))

    def test_filters_cannot_reach_other_company(self):
        other_estate = self.b_obligation.sale.plot.estate_id
        self.assertEqual(self.ids({"estate": other_estate}), set())
        self.assertEqual(self.ids({"q": "Other Buyer"}), set())

    def test_ordering(self):
        self.client.force_authenticate(self.manager)
        rows = self.client.get(self.url("obligation-list"), {"ordering": "-started_on", "page_size": 200}).data["results"]
        starts = [r["started_on"] for r in rows]
        self.assertEqual(starts, sorted(starts, reverse=True))

    def test_list_uses_a_constant_number_of_queries(self):
        self.client.force_authenticate(self.manager)
        with CaptureQueriesContext(connection) as ctx:
            self.client.get(self.url("obligation-list"), {"page_size": 200})
        small = len(ctx)
        for i in range(30):
            self.sale(self.estate, f"X-{i}", f"Buyer {i}", paid=True, paid_ago=3, sold_ago=9)
        with CaptureQueriesContext(connection) as ctx:
            r = self.client.get(self.url("obligation-list"), {"page_size": 200})
        self.assertGreater(r.data["count"], 60)
        self.assertEqual(len(ctx), small)       # no N+1: more rows, same number of queries
        self.assertLessEqual(small, 4)


class ObligationUpdateTests(ObligationApiBase):
    def patch(self, user, pk, payload):
        self.client.force_authenticate(user)
        return self.client.patch(self.url("obligation-detail", pk), payload, format="json")

    def test_manager_updates_and_history_is_written(self):
        r = self.patch(self.manager, self.deed3.pk, {
            "owner": self.staff_legal.pk, "status": "in_progress", "stage": "Internal review",
            "reason": "Awaiting review", "next_action": "Complete review", "next_review_date": "2026-12-01",
            "note": "Picked up"})
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["owner_name"], "Sarah")
        self.assertEqual(r.data["status"], "in_progress")
        self.assertFalse(r.data["is_incomplete"])
        ev = ObligationEvent.objects.filter(obligation=self.deed3).last()
        self.assertEqual(ev.actor, self.manager)
        self.assertEqual(ev.note, "Picked up")
        self.assertEqual(ev.changes["status"], ["not_started", "in_progress"])

    def test_note_only_update_is_logged_and_refreshes_last_updated(self):
        before = ObligationEvent.objects.filter(obligation=self.deed1).count()
        r = self.patch(self.manager, self.deed1.pk, {"note": "Called the solicitor"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(ObligationEvent.objects.filter(obligation=self.deed1).count(), before + 1)
        self.assertEqual(r.data["staleness"], "green")

    def test_empty_or_non_editable_payloads_are_rejected(self):
        self.assertEqual(self.patch(self.manager, self.deed1.pk, {}).status_code, 400)
        r = self.patch(self.manager, self.deed1.pk, {"started_on": "2020-01-01", "sale": 999})
        self.assertEqual(r.status_code, 400)
        self.deed1.refresh_from_db()
        self.assertNotEqual(str(self.deed1.started_on), "2020-01-01")

    def test_cannot_assign_people_or_departments_from_another_company(self):
        self.assertEqual(self.patch(self.manager, self.deed3.pk, {"owner": self.b_manager.pk}).status_code, 400)
        self.assertEqual(self.patch(self.manager, self.deed3.pk, {"department": self.b_legal.pk}).status_code, 400)
        self.assertEqual(self.patch(self.manager, self.deed3.pk, {"status": "nonsense"}).status_code, 400)

    def test_other_company_user_gets_404(self):
        self.assertEqual(self.patch(self.b_manager, self.deed3.pk, {"note": "hi"}).status_code, 404)

    def test_roles(self):
        note = {"note": "x"}
        self.assertEqual(self.patch(self.md, self.deed3.pk, note).status_code, 200)
        self.assertEqual(self.patch(self.sales, self.deed3.pk, note).status_code, 403)
        self.assertEqual(self.patch(self.viewer, self.deed3.pk, note).status_code, 403)
        # staff: own obligation, or their department's; not another department's
        self.assertEqual(self.patch(self.staff_legal, self.deed1.pk, note).status_code, 200)      # owner
        self.assertEqual(self.patch(self.staff_legal, self.deed3.pk, note).status_code, 200)      # Legal department
        self.assertEqual(self.patch(self.staff_survey, self.deed3.pk, note).status_code, 403)
        self.assertEqual(self.patch(self.staff_survey, self.deed1.pk, note).status_code, 403)

    def test_staff_can_claim_an_unassigned_item_in_their_department(self):
        r = self.patch(self.staff_legal, self.deed3.pk, {"owner": self.staff_legal.pk, "next_action": "Start deed"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["owner"], self.staff_legal.pk)

    def test_put_post_delete_not_allowed(self):
        self.client.force_authenticate(self.manager)
        url = self.url("obligation-detail", self.deed1.pk)
        self.assertEqual(self.client.put(url, {}, format="json").status_code, 405)
        self.assertEqual(self.client.delete(url).status_code, 405)
        self.assertEqual(self.client.post(self.url("obligation-list"), {}, format="json").status_code, 405)


class ObligationEventsTests(ObligationApiBase):
    def test_events_in_order_with_names_resolved(self):
        self.client.force_authenticate(self.manager)
        self.client.patch(self.url("obligation-detail", self.deed3.pk),
                          {"owner": self.staff_legal.pk, "department": self.survey.pk, "note": "Reassigned"}, format="json")
        r = self.client.get(reverse("obligation-events", args=[self.deed3.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data[0]["note"], "Created automatically")
        last = r.data[-1]
        self.assertEqual(last["actor_name"], "Mike")
        shown = {c["field"]: c for c in last["changes_display"]}
        self.assertEqual(shown["owner"]["to"], "Sarah")
        self.assertIsNone(shown["owner"]["from"])
        self.assertEqual(shown["department"]["from"], "Legal")
        self.assertEqual(shown["department"]["to"], "Survey")
        times = [e["created_at"] for e in r.data]
        self.assertEqual(times, sorted(times))

    def test_history_visibility_by_role(self):
        url = reverse("obligation-events", args=[self.deed1.pk])
        for user, expected in ((self.md, 200), (self.manager, 200), (self.staff_survey, 200), (self.viewer, 200), (self.sales, 403)):
            self.client.force_authenticate(user)
            self.assertEqual(self.client.get(url).status_code, expected, user.role)

    def test_other_company_history_is_404(self):
        self.client.force_authenticate(self.manager)
        self.assertEqual(self.client.get(reverse("obligation-events", args=[self.b_obligation.pk])).status_code, 404)


class ObligationTypeApiTests(ObligationApiBase):
    def test_everyone_reads_only_own_company_types(self):
        for u in (self.md, self.staff_legal, self.sales, self.viewer):
            self.client.force_authenticate(u)
            r = self.client.get(self.url("obligation-type-list"))
            self.assertEqual(r.status_code, 200, u.role)
            self.assertEqual({row["name"] for row in r.data["results"]}, {"Allocation Letter", "Deed"})

    def test_only_md_and_manager_can_write(self):
        payload = {"name": "Survey Plan", "trigger": "on_full_payment", "sla_days": 60, "default_department": self.survey.pk}
        for u in (self.staff_legal, self.sales, self.viewer):
            self.client.force_authenticate(u)
            self.assertEqual(self.client.post(self.url("obligation-type-list"), payload, format="json").status_code, 403, u.role)
        self.client.force_authenticate(self.manager)
        r = self.client.post(self.url("obligation-type-list"), payload, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(ObligationType.objects.get(pk=r.data["id"]).company, self.a)

    def test_validation(self):
        self.client.force_authenticate(self.manager)
        base = {"trigger": "on_sale", "sla_days": 30}
        post = lambda extra: self.client.post(self.url("obligation-type-list"), {**base, **extra}, format="json").status_code
        self.assertEqual(post({"name": "deed"}), 400)                                  # duplicate (case-insensitive)
        self.assertEqual(post({"name": "X", "sla_days": 0}), 400)
        self.assertEqual(post({"name": "X", "sla_days": 99999}), 400)
        self.assertEqual(post({"name": "X", "default_department": self.b_legal.pk}), 400)   # other company's department
        self.assertEqual(post({"name": "X", "trigger": "whenever"}), 400)

    def test_edit_affects_new_obligations_only_and_delete_is_not_allowed(self):
        before = {o.pk: o.due_date for o in Obligation.objects.filter(obligation_type=self.deed)}
        self.client.force_authenticate(self.manager)
        r = self.client.patch(self.url("obligation-type-detail", self.deed.pk), {"sla_days": 10}, format="json")
        self.assertEqual(r.status_code, 200)
        after = {o.pk: o.due_date for o in Obligation.objects.filter(obligation_type=self.deed)}
        self.assertEqual(before, after)
        plot = Plot.objects.create(company=self.a, estate=self.estate, plot_number="NEW-1")
        buyer = Buyer.objects.create(company=self.a, full_name="New Buyer")
        sale = services.create_sale(self.a, plot, buyer, 100, self.today, 100, self.today)
        self.assertEqual(sale.obligations.get(obligation_type=self.deed).due_date, self.today + timedelta(days=10))
        self.assertEqual(self.client.delete(self.url("obligation-type-detail", self.deed.pk)).status_code, 405)

    def test_deactivated_type_is_not_used_for_new_sales(self):
        self.client.force_authenticate(self.manager)
        self.client.patch(self.url("obligation-type-detail", self.deed.pk), {"is_active": False}, format="json")
        plot = Plot.objects.create(company=self.a, estate=self.estate, plot_number="NEW-2")
        buyer = Buyer.objects.create(company=self.a, full_name="Another Buyer")
        sale = services.create_sale(self.a, plot, buyer, 100, self.today, 100, self.today)
        self.assertFalse(sale.obligations.filter(obligation_type=self.deed).exists())

    def test_other_company_type_is_404(self):
        other = ObligationType.objects.filter(company=self.b).first()
        self.client.force_authenticate(self.manager)
        self.assertEqual(self.client.get(self.url("obligation-type-detail", other.pk)).status_code, 404)
        self.assertEqual(self.client.patch(self.url("obligation-type-detail", other.pk), {"sla_days": 5}, format="json").status_code, 404)
