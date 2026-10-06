from io import StringIO
from datetime import datetime, timezone as dt_timezone
from unittest.mock import patch
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from . import services
from .models import Company, Estate, Obligation, ObligationEvent, Sale, User


def seed(**kwargs):
    out = StringIO()
    kwargs.setdefault("buyers", 60)
    kwargs.setdefault("password", "pw-demo-12345")
    call_command("seed_demo", stdout=out, **kwargs)
    return out.getvalue()


class SeedDemoTests(TestCase):
    def test_creates_a_realistic_spread(self):
        seed()
        co = Company.objects.get(name="Demo Homes")
        self.assertEqual(Sale.objects.filter(company=co).count(), 60)
        self.assertEqual(Estate.objects.filter(company=co).count(), 4)
        for bucket, count in services.ageing(co).items():
            self.assertGreater(count, 0, bucket)
        self.assertGreater(services.overdue(co).count(), 0)
        self.assertGreater(services.stale(co, days=14).count(), 0)
        self.assertGreater(services.exposure(co)["amount"], 0)
        open_obs = services.open_obligations(co)
        self.assertGreater(open_obs.filter(owner__isnull=True).count(), 0)            # incomplete cases exist
        self.assertGreater(open_obs.filter(status="awaiting_external").count(), 0)
        self.assertGreater(Obligation.objects.filter(company=co, status="completed").count(), 0)

    def test_every_obligation_has_coherent_history(self):
        # Time is frozen so this test cannot be affected by the machine clock (e.g. a WSL clock step).
        frozen = datetime(2026, 10, 5, 11, 0, tzinfo=dt_timezone.utc)
        with patch("django.utils.timezone.now", return_value=frozen):
            seed()
            co = Company.objects.get(name="Demo Homes")
            for ob in Obligation.objects.filter(company=co):
                events = list(ob.events.all())
                self.assertTrue(events, ob.pk)
                self.assertLessEqual(ob.last_updated_at, frozen)
                for ev in events:
                    self.assertGreaterEqual(timezone.localtime(ev.created_at).date(), ob.started_on)
                    self.assertLessEqual(ev.created_at, frozen)
                if ob.status == "completed":
                    self.assertIsNotNone(ob.completed_on)
                    self.assertGreaterEqual(ob.completed_on, ob.started_on)

    def test_same_seed_gives_same_data(self):
        seed(company="Demo One", seed=7)
        seed(company="Demo Two", seed=7)
        a = Company.objects.get(name="Demo One")
        b = Company.objects.get(name="Demo Two")
        self.assertEqual(services.exposure(a), services.exposure(b))
        self.assertEqual(services.ageing(a), services.ageing(b))
        def shape(company):
            return sorted(Obligation.objects.filter(company=company).values_list("obligation_type__name", "status", "started_on"))
        self.assertEqual(shape(a), shape(b))

    def test_demo_users_exist_and_log_in_with_given_password(self):
        seed()
        co = Company.objects.get(name="Demo Homes")
        self.assertEqual(set(User.objects.filter(company=co).values_list("role", flat=True)),
                         {"md", "manager", "staff", "sales", "viewer"})
        self.assertTrue(User.objects.get(username="md_demohomes").check_password("pw-demo-12345"))

    def test_random_password_is_shown_once_when_not_given(self):
        out = StringIO()
        call_command("seed_demo", buyers=10, stdout=out)
        self.assertIn("Password (shown once):", out.getvalue())

    def test_refuses_non_demo_company_names(self):
        with self.assertRaises(CommandError):
            seed(company="Acme Properties")

    def test_existing_company_needs_reset(self):
        seed()
        with self.assertRaises(CommandError):
            seed()

    def test_reset_is_refused_when_debug_is_off_unless_forced(self):
        seed()
        with override_settings(DEBUG=False):
            with self.assertRaises(CommandError):
                seed(reset=True)
            seed(reset=True, force=True)
        self.assertEqual(Company.objects.filter(name="Demo Homes").count(), 1)

    @override_settings(DEBUG=True)
    def test_reset_replaces_only_the_demo_company(self):
        real = Company.objects.create(name="Real Developers Ltd")
        Estate.objects.create(company=real, name="Real Estate")
        seed()
        first_ids = set(Sale.objects.filter(company__name="Demo Homes").values_list("id", flat=True))
        seed(reset=True)
        self.assertFalse(first_ids & set(Sale.objects.filter(company__name="Demo Homes").values_list("id", flat=True)))
        self.assertTrue(Estate.objects.filter(company=real).exists())
        self.assertFalse(ObligationEvent.objects.filter(company=real).exists())

class SeedNeverWritesFutureTimestampsTests(TestCase):
    """Regression: running the seed early in the day used to create 'last updated' times in the future."""

    def test_no_timestamp_is_after_now_at_any_time_of_day(self):
        moments = {
            "06:00 Lagos (before 09:00 and 15:00)": datetime(2026, 10, 5, 5, 0, tzinfo=dt_timezone.utc),
            "12:00 Lagos (between 09:00 and 15:00)": datetime(2026, 10, 5, 11, 0, tzinfo=dt_timezone.utc),
            "00:30 Lagos next day": datetime(2026, 10, 5, 23, 30, tzinfo=dt_timezone.utc),
        }
        for label, frozen in moments.items():
            with self.subTest(label), patch("django.utils.timezone.now", return_value=frozen):
                name = f"Demo {label[:5].replace(':', '')}"
                seed(company=name, buyers=40)
                company = Company.objects.get(name=name)
                bad_ob = Obligation.objects.filter(company=company, last_updated_at__gt=frozen)
                bad_ev = ObligationEvent.objects.filter(company=company, created_at__gt=frozen)
                self.assertEqual(bad_ob.count(), 0)
                self.assertEqual(bad_ev.count(), 0)
