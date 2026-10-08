from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from . import services
from .models import Buyer, Company, Department, Estate, Obligation, ObligationEvent, ObligationType, Plot, User


class BulkUpdateServiceTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.co = Company.objects.create(name="A Homes")
        self.other = Company.objects.create(name="B Homes")
        self.legal = Department.objects.create(company=self.co, name="Legal")
        self.manager = User.objects.create_user("mike", password="x", company=self.co, role="manager")
        self.sarah = User.objects.create_user("sarah", password="x", first_name="Sarah", company=self.co, role="staff", department=self.legal)
        self.eve = User.objects.create_user("eve", password="x", company=self.other, role="manager")
        ObligationType.objects.create(company=self.co, name="Deed", trigger="on_full_payment", sla_days=90)
        ObligationType.objects.create(company=self.other, name="Deed", trigger="on_full_payment", sla_days=90)
        self.estate = Estate.objects.create(company=self.co, name="GV")
        self.sales = [self.sale(i) for i in range(4)]
        self.obs = [s.obligations.get() for s in self.sales]

    def sale(self, i, company=None):
        company = company or self.co
        estate = self.estate if company is self.co else Estate.objects.create(company=company, name=f"E{i}")
        plot = Plot.objects.create(company=company, estate=estate, plot_number=f"P-{company.pk}-{i}")
        buyer = Buyer.objects.create(company=company, full_name=f"Buyer {i}")
        return services.create_sale(company, plot, buyer, 100, self.today - timedelta(days=50), 100, self.today - timedelta(days=50))

    def test_applies_to_all_and_each_gets_its_own_history(self):
        ids = [o.pk for o in self.obs]
        updated = services.bulk_update_obligations(ids, self.manager, note="Monday review", owner=self.sarah,
                                                   status="in_progress", next_action="Start the deed")
        self.assertEqual([o.pk for o in updated], ids)
        for ob in Obligation.objects.filter(pk__in=ids):
            self.assertEqual((ob.owner, ob.status, ob.next_action), (self.sarah, "in_progress", "Start the deed"))
            event = ob.events.last()
            self.assertEqual((event.actor, event.note), (self.manager, "Monday review"))
            self.assertEqual(event.changes["owner"], [None, self.sarah.pk])
        self.assertEqual(ObligationEvent.objects.filter(note="Monday review").count(), 4)

    def test_fields_not_mentioned_are_left_alone_and_duplicate_ids_are_ignored(self):
        services.update_obligation(self.obs[0], self.manager, reason="Registry backlog", stage="With registry")
        services.bulk_update_obligations([self.obs[0].pk, self.obs[0].pk], self.manager, next_action="Chase")
        ob = Obligation.objects.get(pk=self.obs[0].pk)
        self.assertEqual((ob.reason, ob.stage, ob.next_action), ("Registry backlog", "With registry", "Chase"))
        self.assertEqual(ob.events.filter(changes__next_action__isnull=False).count(), 1)     # not applied twice

    def test_a_note_alone_is_a_valid_bulk_action(self):
        services.bulk_update_obligations([o.pk for o in self.obs], self.manager, note="Reviewed in the Monday meeting")
        self.assertEqual(ObligationEvent.objects.filter(note="Reviewed in the Monday meeting").count(), 4)

    def test_one_problem_means_nothing_changes(self):
        services.cancel_sale(self.sales[2], self.manager, "defaulted")
        foreign = self.sale(9, self.other).obligations.get()
        ids = [self.obs[0].pk, self.obs[1].pk, self.obs[2].pk, foreign.pk, 999999]
        before = ObligationEvent.objects.count()
        with self.assertRaises(services.BulkUpdateError) as ctx:
            services.bulk_update_obligations(ids, self.manager, owner=self.sarah)
        problems = {p["id"]: p["error"] for p in ctx.exception.problems}
        self.assertEqual(problems, {self.obs[2].pk: "The sale is cancelled.", foreign.pk: "Not found.", 999999: "Not found."})
        self.assertEqual(ObligationEvent.objects.count(), before)
        self.assertFalse(Obligation.objects.filter(owner=self.sarah).exists())

    def test_failure_part_way_rolls_everything_back(self):
        real = services.update_obligation
        calls = {"n": 0}

        def flaky(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 3:
                raise services.SaleStateError("cancelled while we were working")
            return real(*args, **kwargs)

        with patch("tracker.services.update_obligation", side_effect=flaky):
            with self.assertRaises(services.SaleStateError):
                services.bulk_update_obligations([o.pk for o in self.obs], self.manager, owner=self.sarah)
        self.assertFalse(Obligation.objects.filter(owner=self.sarah).exists())

    def test_input_limits(self):
        with self.assertRaises(ValueError):
            services.bulk_update_obligations([], self.manager, owner=self.sarah)
        with self.assertRaises(ValueError):
            services.bulk_update_obligations(list(range(1, services.BULK_LIMIT + 2)), self.manager, owner=self.sarah)
        with self.assertRaises(ValueError):
            services.bulk_update_obligations([self.obs[0].pk], self.manager)
        with self.assertRaises(ValueError):
            services.bulk_update_obligations([self.obs[0].pk], self.manager, started_on="2020-01-01")   # not an editable field
