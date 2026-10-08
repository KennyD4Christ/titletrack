from django.urls import reverse

from api.tests.test_obligations import ObligationApiBase
from tracker import services
from tracker.models import Obligation, ObligationEvent


class BulkUpdateApiTests(ObligationApiBase):
    def bulk(self, user, payload):
        self.client.force_authenticate(user)
        return self.client.post(reverse("obligation-bulk-update"), payload, format="json")

    def ids(self):
        return [self.deed1.pk, self.deed3.pk, self.alloc1.pk]

    def test_only_md_and_manager_may_bulk_update(self):
        payload = {"ids": [self.deed3.pk], "note": "x"}
        for user, code in ((self.md, 200), (self.manager, 200), (self.staff_legal, 403), (self.sales, 403),
                           (self.viewer, 403), (self.outsider, 403)):
            self.assertEqual(self.bulk(user, payload).status_code, code, user.role)
        self.client.force_authenticate(None)
        self.assertIn(self.client.post(reverse("obligation-bulk-update"), payload, format="json").status_code, (401, 403))

    def test_assigning_many_gives_each_one_history_and_shows_in_lists(self):
        r = self.bulk(self.manager, {"ids": self.ids(), "owner": self.staff_survey.pk, "next_action": "Pick up",
                                     "note": "Reallocated after Monday review"})
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual((r.data["updated"], sorted(r.data["ids"])), (3, sorted(self.ids())))
        for pk in self.ids():
            ob = Obligation.objects.get(pk=pk)
            self.assertEqual((ob.owner, ob.next_action), (self.staff_survey, "Pick up"))
        events = self.client.get(reverse("obligation-events", args=[self.deed1.pk])).data
        self.assertEqual(events[-1]["note"], "Reallocated after Monday review")
        self.assertEqual(events[-1]["actor_name"], "Mike")
        mine = self.client.get(reverse("obligation-list"), {"owner": self.staff_survey.pk}).data
        self.assertEqual(mine["count"], 3)

    def test_validation(self):
        cases = {
            "no ids": {"ids": [], "owner": self.staff_legal.pk},
            "missing ids": {"owner": self.staff_legal.pk},
            "ids not numbers": {"ids": ["a"], "owner": self.staff_legal.pk},
            "too many": {"ids": list(range(1, services.BULK_LIMIT + 2)), "owner": self.staff_legal.pk},
            "nothing to change": {"ids": self.ids()},
            "owner from another company": {"ids": self.ids(), "owner": self.b_manager.pk},
            "bad status": {"ids": self.ids(), "status": "finished"},
        }
        for label, payload in cases.items():
            self.assertEqual(self.bulk(self.manager, payload).status_code, 400, label)
        self.assertFalse(Obligation.objects.filter(owner=self.b_manager).exists())

    def test_foreign_missing_or_cancelled_ids_stop_the_whole_batch(self):
        self.client.force_authenticate(self.manager)
        self.client.post(reverse("sale-cancel", args=[self.s3.pk]), {"reason": "defaulted"}, format="json")
        before = ObligationEvent.objects.count()
        r = self.bulk(self.manager, {"ids": [self.deed1.pk, self.deed3.pk, self.b_obligation.pk, 999999], "owner": self.staff_legal.pk})
        self.assertEqual(r.status_code, 409)
        self.assertEqual({p["id"]: p["error"] for p in r.data["problems"]},
                         {self.deed3.pk: "The sale is cancelled.", self.b_obligation.pk: "Not found.", 999999: "Not found."})
        self.assertEqual(ObligationEvent.objects.count(), before)
        self.assertEqual(Obligation.objects.get(pk=self.deed1.pk).owner, self.staff_legal)   # unchanged: it was already hers


class CanEditFlagTests(ObligationApiBase):
    def flag(self, user, pk=None, listing=True):
        self.client.force_authenticate(user)
        if listing:
            rows = self.client.get(reverse("obligation-list"), {"page_size": 200, "include_cancelled": "1"}).data["results"]
            return {r["id"]: r["can_edit"] for r in rows}[pk]
        return self.client.get(reverse("obligation-detail", args=[pk])).data["can_edit"]

    def test_flag_follows_role_ownership_and_department(self):
        for listing in (True, False):
            self.assertTrue(self.flag(self.manager, self.deed3.pk, listing))
            self.assertTrue(self.flag(self.md, self.deed3.pk, listing))
            self.assertTrue(self.flag(self.staff_legal, self.deed1.pk, listing))       # owner
            self.assertTrue(self.flag(self.staff_legal, self.deed3.pk, listing))       # Legal department
            self.assertFalse(self.flag(self.staff_survey, self.deed3.pk, listing))     # another department
            self.assertFalse(self.flag(self.sales, self.deed3.pk, listing))
            self.assertFalse(self.flag(self.viewer, self.deed3.pk, listing))

    def test_flag_is_false_once_the_sale_is_cancelled(self):
        self.client.force_authenticate(self.manager)
        self.client.post(reverse("sale-cancel", args=[self.s3.pk]), {"reason": "defaulted"}, format="json")
        for listing in (True, False):
            self.assertFalse(self.flag(self.manager, self.deed3.pk, listing))

    def test_flag_matches_what_patch_actually_allows(self):
        for user in (self.md, self.manager, self.staff_legal, self.staff_survey, self.sales, self.viewer):
            for pk in (self.deed1.pk, self.deed3.pk):
                allowed = self.flag(user, pk, listing=False)
                self.client.force_authenticate(user)
                code = self.client.patch(reverse("obligation-detail", args=[pk]), {"note": "probe"}, format="json").status_code
                self.assertEqual(code == 200, allowed, f"{user.role} {pk} -> {code}")
