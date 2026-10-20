import random
from collections import Counter
from decimal import Decimal
from io import StringIO
from datetime import date, datetime, time, timedelta, timezone as dt_timezone
from unittest.mock import patch
from django.apps import apps
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from . import services
from .models import (
    Buyer, Company, Department, Estate, Obligation, ObligationEvent, ObligationType, Payment, Plot, Sale, SaleEvent,
    User,
)
from .management.commands.seed_demo import Command as SeedCommand
from .seed import names, scenarios


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


# ---------------------------------------------------------------------------------------------------------
# Batched seeder: equivalence with the real services, invariants, dry-run and rollback.
# The oracle below is the seeder as it was BEFORE batching: every sale and obligation goes through
# services.create_sale / services.update_obligation, one row at a time. It is test code on purpose, so the
# batched command can never silently drift from the services' rules (it is slow, so tests keep sizes small).
# ---------------------------------------------------------------------------------------------------------

FAST_HASH = ["django.contrib.auth.hashers.MD5PasswordHasher"]      # speeds up the 7 demo users; no effect on data
FROZEN = datetime(2026, 10, 19, 11, 0, tzinfo=dt_timezone.utc)


def _at(day, hour=9):
    return min(timezone.make_aware(datetime.combine(day, time(hour, 0))), timezone.now())


def reference_seed(name, buyers, seed_value):
    rng = random.Random(seed_value)
    today = timezone.localdate()
    company = Company.objects.create(name=name)
    suffix = name.lower().replace(" ", "")
    departments = {n: Department.objects.create(company=company, name=n) for n in scenarios.DEPARTMENTS}

    def make_user(base, role, dept=None):
        return User.objects.create_user(
            f"{base}_{suffix}", password="pw-demo-12345", company=company, role=role,
            department=departments.get(dept), first_name=names.DEMO_USERS[base][0], last_name=names.DEMO_USERS[base][1])

    for base in ("md", "manager", "sales", "viewer"):
        make_user(base, base)
    staff = {departments["Documentation"].id: make_user("docs", "staff", "Documentation"),
             departments["Survey"].id: make_user("survey", "staff", "Survey"),
             departments["Legal"].id: make_user("legal", "staff", "Legal")}
    for oname, trigger, sla, dept in scenarios.OBLIGATION_TYPES:
        ObligationType.objects.create(company=company, name=oname, trigger=trigger, sla_days=sla,
                                      default_department=departments[dept])
    estates = [(Estate.objects.create(company=company, name=n, location=loc), prefix, rng_)
               for n, loc, prefix, rng_ in names.ESTATES]
    per_estate = [buyers // len(estates) + (1 if i < buyers % len(estates) else 0) for i in range(len(estates))]
    plots = {}
    for (estate, prefix, _), count in zip(estates, per_estate):
        plots[estate.id] = [Plot.objects.create(company=company, estate=estate, plot_number=f"{prefix}-{i:03d}")
                            for i in range(1, count + max(2, count // 10) + 1)]
    mix_names = [m for m, _ in scenarios.PAYMENT_MIX]
    mix_weights = [w for _, w in scenarios.PAYMENT_MIX]
    for i in range(buyers):
        estate, _, (low, high) = estates[i % len(estates)]
        plot = plots[estate.id].pop(0)
        buyer = Buyer.objects.create(company=company,
                                     full_name=f"{rng.choice(names.FIRST_NAMES)} {rng.choice(names.LAST_NAMES)}")
        price = Decimal(rng.randrange(low, high + 1, 250_000))
        kind = rng.choices(mix_names, mix_weights)[0]
        if kind == "partial":
            pct = rng.choice([20, 30, 40, 50, 60, 70, 80])
            paid = Decimal(int(price) * pct // 100 // 10_000 * 10_000)
            services.create_sale(company, plot, buyer, price, today - timedelta(days=rng.randint(10, 300)), paid)
        else:
            lo, hi = {"recent": (0, 45), "mid": (46, 200), "old": (201, 600)}[kind]
            paid_on = today - timedelta(days=rng.randint(lo, hi))
            services.create_sale(company, plot, buyer, price, paid_on - timedelta(days=rng.randint(30, 400)),
                                 price, paid_on)
    for ob in Obligation.objects.filter(company=company).select_related("obligation_type", "sale").order_by(
            "sale_id", "obligation_type_id"):
        n = ob.obligation_type.name
        age = (today - ob.started_on).days
        actor = staff[ob.department_id]
        first_event = ob.events.order_by("created_at", "id").first()
        touch_dt, completed_on = _at(ob.started_on), None
        if rng.random() < scenarios.COMPLETION_CHANCE[n] * (0.4 if age < 30 else 1.0):
            lo = min(5, age)
            hi = max(lo, min(age, ob.obligation_type.sla_days + 45))
            touch_day = ob.started_on + timedelta(days=rng.randint(lo, hi))
            services.update_obligation(ob, actor, owner=actor, status="completed", stage="Delivered to buyer",
                                       note="Delivered to buyer")
            touch_dt, completed_on = _at(touch_day, 15), touch_day
        else:
            r = rng.random()
            if r < 0.35 or r < 0.70:
                r2 = rng.random()
                days_ago = rng.randint(0, 7) if r2 < 0.45 else rng.randint(8, 14) if r2 < 0.70 else rng.randint(15, 60)
                touch_day = today - timedelta(days=min(days_ago, age))
                if r < 0.35:
                    party = rng.choice(scenarios.EXTERNAL_PARTIES[n])
                    services.update_obligation(
                        ob, actor, owner=actor, status="awaiting_external", stage=f"With {party}",
                        external_party=party, reason=rng.choice(scenarios.EXTERNAL_REASONS),
                        next_action=rng.choice(scenarios.NEXT_ACTIONS_EXTERNAL),
                        next_review_date=today + timedelta(days=rng.randint(-10, 21)), note="Submitted externally")
                else:
                    services.update_obligation(
                        ob, actor, owner=actor, status="in_progress", stage=rng.choice(scenarios.INTERNAL_STAGES),
                        reason=rng.choice(scenarios.INTERNAL_REASONS),
                        next_action=rng.choice(scenarios.NEXT_ACTIONS_INTERNAL),
                        next_review_date=today + timedelta(days=rng.randint(-5, 14)), note="Work started")
                touch_dt = _at(touch_day, 15)
        ObligationEvent.objects.filter(pk=first_event.pk).update(created_at=_at(ob.started_on))
        ObligationEvent.objects.filter(obligation=ob).exclude(pk=first_event.pk).update(created_at=touch_dt)
        changes = {"last_updated_at": touch_dt}
        if completed_on:
            changes["completed_on"] = completed_on
        Obligation.objects.filter(pk=ob.pk).update(**changes)
    return company


def snapshot(company):
    """Everything about one demo company that is not an id, with user ids replaced by login names."""
    user_names = {u.pk: u.username.rsplit("_", 1)[0] for u in User.objects.filter(company=company)}

    def user(pk):
        return user_names[pk] if pk else None

    def fix(changes):
        return {k: [user(v) for v in vals] if k == "owner" else vals for k, vals in changes.items()}

    sales = list(Sale.objects.filter(company=company).select_related("plot__estate", "buyer").order_by("id"))
    sale_no = {s.pk: i for i, s in enumerate(sales)}
    obs = list(Obligation.objects.filter(company=company).select_related("obligation_type", "department")
               .order_by("sale_id", "obligation_type_id"))
    key = {o.pk: (sale_no[o.sale_id], o.obligation_type.name) for o in obs}
    return {
        "departments": sorted(company.departments.values_list("name", flat=True)),
        "users": sorted((user(u.pk), u.role, u.first_name, u.last_name, u.department and u.department.name,
                         u.is_active, u.email) for u in User.objects.filter(company=company).select_related("department")),
        "types": sorted((t.name, t.trigger, t.sla_days, t.default_department.name, t.is_active)
                        for t in ObligationType.objects.filter(company=company).select_related("default_department")),
        "estates": sorted(Estate.objects.filter(company=company).values_list("name", "location")),
        "plots": sorted(Plot.objects.filter(company=company).values_list("estate__name", "plot_number")),
        "buyers": list(Buyer.objects.filter(company=company).order_by("id").values_list("full_name", "phone", "email")),
        "sales": [(s.plot.estate.name, s.plot.plot_number, s.buyer.full_name, s.price, s.amount_paid, s.sale_date,
                   s.fully_paid_on, s.status, s.cancelled_at, s.cancelled_by_id, s.cancel_reason) for s in sales],
        "payments": [(sale_no[p.sale_id], p.kind, p.amount, p.paid_on, p.reference, p.note, p.reverses_id,
                      user(p.recorded_by_id), p.idempotency_key, p.created_at)
                     for p in Payment.objects.filter(company=company).order_by("id")],
        "sale_events": [(sale_no[e.sale_id], e.action, e.changes, e.note, user(e.actor_id), e.created_at)
                        for e in SaleEvent.objects.filter(company=company).order_by("id")],
        "obligations": [(key[o.pk], o.status, o.stage, user(o.owner_id), o.department.name, o.external_party,
                         o.reason, o.next_action, o.next_review_date, o.started_on, o.due_date, o.completed_on,
                         o.last_updated_at, o.created_at) for o in obs],
        "obligation_events": [row[:1] + row[2:] for row in sorted(
            ((key[e.obligation_id], e.pk, user(e.actor_id), fix(e.changes), e.note, e.created_at)
             for e in ObligationEvent.objects.filter(company=company)), key=lambda r: (r[0], r[1]))],
    }


@override_settings(PASSWORD_HASHERS=FAST_HASH)
class BatchedSeedMatchesServicesTests(TestCase):
    """The batched seeder must produce exactly what the real services produce for the same seed."""

    def test_same_data_as_the_one_row_at_a_time_services(self):
        # batch_size 10 and 33 force many chunk boundaries (rows must still land in the same order)
        for i, (buyers, seed_value, batch) in enumerate([(8, 1, 250), (40, 42, 250), (97, 7, 10), (61, 3, 33)]):
            with self.subTest(buyers=buyers, seed=seed_value, batch=batch), \
                    patch("django.utils.timezone.now", return_value=FROZEN):
                ref = reference_seed(f"Demo Ref{i}", buyers, seed_value)
                seed(company=f"Demo Bat{i}", buyers=buyers, seed=seed_value, batch_size=batch)
                got = snapshot(Company.objects.get(name=f"Demo Bat{i}"))
                want = snapshot(ref)
                for part in want:
                    self.assertEqual(got[part], want[part], part)
                self.assertGreater(len(want["obligation_events"]), len(want["obligations"]))   # some were progressed

    def test_the_comparison_notices_a_different_seed(self):
        with patch("django.utils.timezone.now", return_value=FROZEN):
            seed(company="Demo One", buyers=40, seed=1)
            seed(company="Demo Two", buyers=40, seed=2)
        a, b = (snapshot(Company.objects.get(name=n)) for n in ("Demo One", "Demo Two"))
        self.assertNotEqual(a["sales"], b["sales"])
        self.assertNotEqual(a["obligations"], b["obligations"])

    def test_same_seed_twice_is_identical(self):
        with patch("django.utils.timezone.now", return_value=FROZEN):
            seed(company="Demo One", buyers=60, seed=5)
            seed(company="Demo Two", buyers=60, seed=5)
        self.assertEqual(snapshot(Company.objects.get(name="Demo One")), snapshot(Company.objects.get(name="Demo Two")))


@override_settings(PASSWORD_HASHERS=FAST_HASH)
class BatchedSeedInvariantTests(TestCase):
    """Rules that must hold whatever the code path, checked directly on the stored rows."""

    @classmethod
    def setUpTestData(cls):
        cls.other = Company.objects.create(name="Real Developers Ltd")
        Estate.objects.create(company=cls.other, name="Real Estate")
        with patch("django.utils.timezone.now", return_value=FROZEN):
            seed(company="Demo Inv", buyers=80, seed=11)
        cls.co = Company.objects.get(name="Demo Inv")

    def test_sales_and_the_payment_ledger(self):
        sales = list(Sale.objects.filter(company=self.co))
        self.assertEqual(len(sales), 80)
        for s in sales:
            self.assertEqual(s.status, Sale.Status.ACTIVE, s.pk)                    # nothing is cancelled
            self.assertEqual(services.ledger_total(s), s.amount_paid, s.pk)         # ledger equals stored total
            kinds = list(s.payments.values_list("kind", "amount"))
            self.assertEqual(kinds, [("opening", s.amount_paid)] if s.amount_paid > 0 else [], s.pk)
            self.assertEqual(s.fully_paid_on is not None, s.amount_paid >= s.price, s.pk)
            if s.fully_paid_on:
                self.assertGreaterEqual(s.fully_paid_on, s.sale_date)
            self.assertLessEqual(s.amount_paid, s.price)
            events = list(s.events.all())
            self.assertEqual([e.action for e in events], ["created"], s.pk)
            self.assertEqual(events[0].changes, {"price": [None, f"{s.price:.2f}"],
                                                 "amount_paid": [None, f"{s.amount_paid:.2f}"]})
            self.assertIsNone(events[0].actor_id)
        self.assertEqual(Payment.objects.filter(company=self.co).count(), sum(1 for s in sales if s.amount_paid > 0))
        unsold = Plot.objects.filter(company=self.co, sales__isnull=True).count()
        self.assertEqual(Plot.objects.filter(company=self.co).count(), 80 + unsold)       # every sale has its own plot
        self.assertGreaterEqual(unsold, 2 * len(names.ESTATES))                             # and some plots stay unsold

    def test_each_sale_has_exactly_the_obligations_it_is_entitled_to(self):
        types = list(ObligationType.objects.filter(company=self.co))
        for s in Sale.objects.filter(company=self.co):
            want = {t.pk for t in types if t.trigger == "on_sale" or s.fully_paid_on is not None}
            obs = list(s.obligations.select_related("obligation_type", "department"))
            self.assertEqual({o.obligation_type_id for o in obs}, want, s.pk)
            self.assertEqual(len(obs), len(want))                                    # none duplicated
            for o in obs:
                t = o.obligation_type
                start = s.fully_paid_on if t.trigger == "on_full_payment" else s.sale_date
                self.assertEqual(o.started_on, start)
                self.assertEqual(o.due_date, start + timedelta(days=t.sla_days))
                self.assertEqual(o.department_id, t.default_department_id)           # department assignment

    def test_company_isolation(self):
        co = self.co
        self.assertFalse(Sale.objects.filter(company=co).exclude(plot__company=co).exists())
        self.assertFalse(Sale.objects.filter(company=co).exclude(buyer__company=co).exists())
        self.assertFalse(Plot.objects.filter(company=co).exclude(estate__company=co).exists())
        self.assertFalse(Payment.objects.filter(company=co).exclude(sale__company=co).exists())
        self.assertFalse(SaleEvent.objects.filter(company=co).exclude(sale__company=co).exists())
        self.assertFalse(Obligation.objects.filter(company=co).exclude(sale__company=co).exists())
        self.assertFalse(Obligation.objects.filter(company=co).exclude(obligation_type__company=co).exists())
        self.assertFalse(Obligation.objects.filter(company=co).exclude(department__company=co).exists())
        self.assertFalse(Obligation.objects.filter(company=co, owner__isnull=False).exclude(owner__company=co).exists())
        self.assertFalse(ObligationEvent.objects.filter(company=co).exclude(obligation__company=co).exists())
        self.assertFalse(ObligationEvent.objects.filter(company=co, actor__isnull=False).exclude(actor__company=co).exists())
        for model in (Sale, Payment, SaleEvent, Obligation, ObligationEvent, Buyer, Plot):
            self.assertFalse(model.objects.filter(company=self.other).exists(), model.__name__)   # the real company is untouched
        self.assertEqual(Estate.objects.filter(company=self.other).count(), 1)

    @patch("django.utils.timezone.now", return_value=FROZEN)       # the data was seeded at this moment
    def test_obligation_states_and_history(self, _now):
        frozen = FROZEN
        seen = Counter()
        for ob in Obligation.objects.filter(company=self.co).select_related("obligation_type", "owner"):
            events = list(ob.events.all())                                          # ordered by (created_at, id)
            self.assertIn(len(events), (1, 2), ob.pk)
            first = events[0]
            self.assertEqual((first.note, first.actor_id, first.changes), ("Created automatically", None, {}))
            self.assertEqual(first.created_at, _at(ob.started_on))
            self.assertLessEqual(ob.last_updated_at, frozen)
            self.assertEqual(ob.last_updated_at, events[-1].created_at)
            if len(events) == 1:                                                    # untouched: stays incomplete
                seen["untouched"] += 1
                self.assertEqual((ob.status, ob.owner_id, ob.stage, ob.next_action, ob.completed_on),
                                 ("not_started", None, "", "", None))
                self.assertEqual(ob.last_updated_at, _at(ob.started_on))
                continue
            second = events[1]
            seen[ob.status] += 1
            self.assertEqual(second.actor_id, ob.owner_id)
            self.assertEqual(second.actor.department_id, ob.department_id)
            self.assertEqual(second.changes["status"], ["not_started", ob.status])  # before/after is right
            self.assertEqual(second.changes["owner"], [None, ob.owner_id])
            self.assertEqual(set(second.changes) - {"owner", "status", "stage", "external_party", "reason",
                                                    "next_action", "next_review_date"}, set())
            self.assertGreaterEqual(second.created_at, first.created_at)
            self.assertLessEqual(timezone.localtime(second.created_at).date(), timezone.localdate())
            if ob.status == "completed":
                self.assertEqual(second.note, "Delivered to buyer")
                self.assertIsNotNone(ob.completed_on)
                self.assertGreaterEqual(ob.completed_on, ob.started_on)
                self.assertEqual(second.created_at, _at(ob.completed_on, 15))
            else:
                self.assertIsNone(ob.completed_on)
                self.assertIn(ob.status, ("in_progress", "awaiting_external"))
                self.assertEqual(ob.external_party != "", ob.status == "awaiting_external")
                self.assertTrue(ob.stage and ob.reason and ob.next_action and ob.next_review_date)
        for state in ("untouched", "completed", "in_progress", "awaiting_external"):
            self.assertGreater(seen[state], 0, state)

    def test_creation_event_is_first_by_id(self):
        for ob in Obligation.objects.filter(company=self.co):
            ids = list(ob.events.order_by("id").values_list("note", flat=True))
            self.assertEqual(ids[0], "Created automatically", ob.pk)                 # creation event is always first


class SeedStatementBudgetTests(TestCase):
    """Diagnostic guards (NOT correctness proofs) for the two things that make a seed slow on a hosted
    database: the NUMBER of round trips and the BYTES sent. On a slow uplink the bytes dominate (measured:
    about 0.3 s per KB), so rows must be inserted once, in their final state, never written then UPDATEd."""

    @override_settings(PASSWORD_HASHERS=FAST_HASH)
    def test_statement_count_is_nearly_flat(self):
        def run(name, buyers):
            with CaptureQueriesContext(connection) as ctx:
                seed(company=name, buyers=buyers)
            return len(ctx)
        small, large = run("Demo Small", 16), run("Demo Large", 160)
        self.assertLess(small, 150)
        self.assertLess(large - small, 50, (small, large))

    @override_settings(PASSWORD_HASHERS=FAST_HASH)
    def test_rows_are_inserted_once_and_bytes_stay_small(self):
        with CaptureQueriesContext(connection) as ctx:
            seed(company="Demo Bytes", buyers=160)
        sql = [q["sql"] for q in ctx.captured_queries]
        updates = [q for q in sql if q.lstrip().upper().startswith("UPDATE")]
        self.assertEqual(updates, [], "seeding must never UPDATE rows it has just inserted")
        sent = sum(len(q.encode()) for q in sql)
        self.assertLess(sent / 160, 3_200, f"{sent / 160:.0f} bytes per buyer")      # about 2,300 today

    @override_settings(PASSWORD_HASHERS=FAST_HASH)
    def test_batch_size_is_validated_and_limits_statement_rows(self):
        for bad in (0, 9, 5001):
            with self.assertRaises(CommandError):
                seed(buyers=10, batch_size=bad)
        with CaptureQueriesContext(connection) as ctx:
            seed(company="Demo Chunks", buyers=40, batch_size=10)
        inserts = [q for q in ctx.captured_queries if q["sql"].lstrip().startswith('INSERT INTO "tracker_obligationevent"')]
        self.assertGreater(len(inserts), 3)                  # events were split into several statements


@override_settings(PASSWORD_HASHERS=FAST_HASH)
class SeedDryRunAndRollbackTests(TransactionTestCase):
    """TransactionTestCase, so the seed runs in a REAL outermost transaction (commit/rollback, not a savepoint)."""

    def counts(self):
        return {m.__name__: m.objects.count() for m in apps.get_app_config("tracker").get_models()}

    def setUp(self):
        real = Company.objects.create(name="Real Developers Ltd")
        Estate.objects.create(company=real, name="Real Estate")
        self.before = self.counts()

    def test_dry_run_saves_nothing(self):
        out = seed(dry_run=True, buyers=30)
        self.assertEqual(self.counts(), self.before)
        self.assertFalse(Company.objects.filter(name="Demo Homes").exists())
        self.assertIn("DRY RUN", out)
        self.assertIn("SQL statements", out)
        self.assertIn("KB sent", out)
        self.assertNotIn("Password (shown once)", out)

    def test_dry_run_with_a_random_password_saves_nothing(self):
        out = StringIO()
        call_command("seed_demo", buyers=10, dry_run=True, stdout=out)
        self.assertEqual(self.counts(), self.before)
        self.assertNotIn("Password (shown once)", out.getvalue())

    def test_an_error_during_a_dry_run_leaves_nothing(self):
        with patch.object(ObligationEvent.objects, "bulk_create", side_effect=RuntimeError("boom")):   # the last write
            with self.assertRaises(RuntimeError):
                seed(dry_run=True, buyers=20)
        self.assertEqual(self.counts(), self.before)

    def test_an_error_during_a_real_run_leaves_nothing(self):
        for target in (Obligation.objects, ObligationEvent.objects, Sale.objects, Payment.objects):
            with self.subTest(target=target.model.__name__):
                with patch.object(target, "bulk_create", side_effect=RuntimeError("boom")):
                    with self.assertRaises(RuntimeError):
                        seed(buyers=20)
                self.assertEqual(self.counts(), self.before)

    def test_created_at_stamping_is_restored_after_success_and_after_failure(self):
        flag = lambda: ObligationEvent._meta.get_field("created_at").auto_now_add
        self.assertTrue(flag())
        seed(buyers=12)
        self.assertTrue(flag())
        with patch.object(ObligationEvent.objects, "bulk_create", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                seed(company="Demo Fails", buyers=12)
        self.assertTrue(flag())
        # and a normal edit made afterwards is stamped with the real time, not an old seeded value
        company = Company.objects.get(name="Demo Homes")
        ob = Obligation.objects.filter(company=company).first()
        before = timezone.now()
        event = ObligationEvent.objects.create(company=company, obligation=ob, note="after seeding")
        self.assertGreaterEqual(event.created_at, before)

    def test_dry_run_cannot_be_combined_with_reset(self):
        seed(buyers=12)
        after_real = self.counts()
        with self.assertRaises(CommandError):
            seed(dry_run=True, reset=True, force=True, buyers=12)
        self.assertEqual(self.counts(), after_real)

    def test_a_real_run_still_works_after_a_dry_run(self):
        seed(dry_run=True, buyers=20)
        seed(buyers=20)
        self.assertEqual(Sale.objects.filter(company__name="Demo Homes").count(), 20)
        self.assertEqual(Company.objects.filter(name="Demo Homes").count(), 1)

    def test_dry_run_refuses_an_existing_company_without_touching_it(self):
        seed(buyers=12)
        after_real = self.counts()
        with self.assertRaises(CommandError):
            seed(dry_run=True, buyers=12)
        self.assertEqual(self.counts(), after_real)


@override_settings(PASSWORD_HASHERS=FAST_HASH)
class ApplyUpdateParityTests(TestCase):
    """The seeder updates obligations in memory (SeedCommand.apply_update). It must behave exactly like the
    real services.update_obligation: same guards, same before/after diff, same completed_on rule."""

    def setUp(self):
        self.co = Company.objects.create(name="Demo Parity")
        dept = Department.objects.create(company=self.co, name="Legal")
        self.actor = User.objects.create_user("p_actor", password="x", company=self.co, role="staff", department=dept)
        self.stranger = User.objects.create_user("p_stranger", password="x", role="staff",
                                                 company=Company.objects.create(name="Demo Other"))
        ObligationType.objects.create(company=self.co, name="Receipt", trigger="on_sale", sla_days=30,
                                      default_department=dept)
        estate = Estate.objects.create(company=self.co, name="E", location="L")
        self.pks = []
        for n in range(24):
            plot = Plot.objects.create(company=self.co, estate=estate, plot_number=f"P{n}")
            buyer = Buyer.objects.create(company=self.co, full_name=f"B{n}")
            sale = services.create_sale(self.co, plot, buyer, Decimal(1000), date(2026, 1, 5), Decimal(100))
            self.pks.append(sale.obligations.get().pk)
        self.next = iter(self.pks)

    def pair(self):
        """Two identical obligations: one for the service, one for the in-memory twin."""
        get = lambda pk: Obligation.objects.select_related("sale", "obligation_type").get(pk=pk)
        return get(next(self.next)), get(next(self.next))

    def both(self, actor=None, note="", **changes):
        actor = actor or self.actor
        svc, twin = self.pair()
        outcomes, returned = [], []
        for run in (lambda: services.update_obligation(svc, actor, note=note, **changes),
                    lambda: SeedCommand().apply_update(self.co, twin, actor, note, changes)):
            try:
                returned.append(run())
                outcomes.append(None)
            except Exception as exc:                                    # compare the kind of failure too
                returned.append(None)
                outcomes.append(type(exc))
        self.twin_event = returned[1]
        return svc, twin, outcomes

    @patch("django.utils.timezone.now", return_value=FROZEN)
    def test_same_result_and_same_event(self, _now):
        cases = [
            ("Delivered", dict(owner=None, status="completed", stage="Delivered to buyer")),
            ("Submitted", dict(status="awaiting_external", stage="With Registry", external_party="Registry",
                               reason="Awaiting stamp", next_action="Chase", next_review_date=date(2026, 11, 1))),
            ("Started", dict(status="in_progress", stage="Drafting", reason="In queue", next_action="Prepare")),
            ("", dict(stage="Only a stage")),
            ("note only", dict(status="not_started")),                   # nothing changes, but there is a note
        ]
        for note, changes in cases:
            with self.subTest(note=note, changes=sorted(changes)):
                if "owner" in changes:
                    changes = {**changes, "owner": self.actor}
                svc, twin, outcomes = self.both(note=note, **changes)
                self.assertEqual(outcomes, [None, None])
                svc = Obligation.objects.get(pk=svc.pk)
                for field in (*services.TRACKED_FIELDS, "completed_on"):
                    self.assertEqual(services._ser(getattr(svc, field)), services._ser(getattr(twin, field)), field)
                real = list(svc.events.exclude(note="Created automatically").order_by("id"))
                event = self.twin_event
                if real:
                    self.assertEqual(len(real), 1)
                    self.assertEqual((event.changes, event.note, event.actor_id, event.company_id),
                                     (real[0].changes, real[0].note, real[0].actor_id, real[0].company_id))
                else:
                    self.assertIsNone(event)

    def test_nothing_to_record_returns_no_event(self):
        svc, twin, outcomes = self.both(status="not_started")
        self.assertEqual(outcomes, [None, None])
        self.assertEqual(svc.events.exclude(note="Created automatically").count(), 0)
        self.assertIsNone(self.twin_event)

    def test_same_guards(self):
        svc, twin, outcomes = self.both(actor=self.stranger, status="in_progress")      # another company's user
        self.assertEqual(outcomes, [PermissionDenied, PermissionDenied])
        a, b = self.pair()
        Sale.objects.filter(pk__in=[a.sale_id, b.sale_id]).update(status=Sale.Status.CANCELLED)
        a, b = (Obligation.objects.select_related("sale").get(pk=o.pk) for o in (a, b))
        results = []
        for run in (lambda: services.update_obligation(a, self.actor, status="in_progress"),
                    lambda: SeedCommand().apply_update(self.co, b, self.actor, "", {"status": "in_progress"})):
            with self.assertRaises(services.SaleStateError):                            # cancelled sale is read-only
                run()
        for run in (lambda: services.update_obligation(self.pair()[0], self.actor, due_date=date(2030, 1, 1)),
                    lambda: SeedCommand().apply_update(self.co, self.pair()[1], self.actor, "", {"due_date": date(2030, 1, 1)})):
            with self.assertRaises(ValueError):                                         # only tracked fields
                run()
