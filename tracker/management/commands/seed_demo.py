import random
from contextlib import contextmanager
from datetime import datetime, time, timedelta
from decimal import Decimal
from time import monotonic

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone
from django.utils.crypto import get_random_string
from django.utils.text import slugify

from tracker import services
from tracker.models import (
    Buyer, Company, Department, Estate, Obligation, ObligationEvent, ObligationType, Payment, Plot, Sale,
    SaleEvent, User,
)
from tracker.seed import names, scenarios

# Rows per INSERT. Bounded so no single statement gets huge, and so a slow link shows steady progress.
DEFAULT_BATCH = 250


def at(day, hour=9):
    """A timezone-aware datetime on the given local date, never later than 'now'.
    (Without the clamp, seeding in the morning would create 'last updated' times later today,
    i.e. in the future.)"""
    moment = timezone.make_aware(datetime.combine(day, time(hour, 0)))
    return min(moment, timezone.now())


@contextmanager
def explicit_created_at(model):
    """auto_now_add overwrites any created_at passed to an INSERT, which would force a second UPDATE pass to
    backdate history (hundreds of KB over the wire). Switch it off for ONE insert, then always switch it back
    on. Only the seeder backdates; this is a plain attribute flip in a single-process command."""
    field = model._meta.get_field("created_at")
    saved = field.auto_now_add
    field.auto_now_add = False
    try:
        yield
    finally:
        field.auto_now_add = saved


class _DryRunRollback(Exception):
    """Raised on purpose at the end of a --dry-run so the surrounding transaction is rolled back."""


class Command(BaseCommand):
    help = (
        "Create a synthetic DEMO company (never real data). Rows are written in batches, following the same "
        "rules as tracker.services (sale, opening payment, history, obligations), so history and rules are "
        "real. tracker/test_seed.py checks the result against the one-row-at-a-time services."
    )

    def add_arguments(self, parser):
        parser.add_argument("--company", default="Demo Homes", help="Must start with 'Demo'.")
        parser.add_argument("--buyers", type=int, default=300)
        parser.add_argument("--seed", type=int, default=42, help="Same seed gives the same data.")
        parser.add_argument("--password", default=None, help="Password for demo users (random if omitted).")
        parser.add_argument("--reset", action="store_true", help="Delete the existing demo company first.")
        parser.add_argument("--force", action="store_true", help="Allow --reset when DEBUG is off (demo servers only).")
        parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH,
                            help=f"Rows per INSERT statement (default {DEFAULT_BATCH}).")
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Do the whole seed, report statement count and time, then ROLL BACK: nothing is saved.")

    def handle(self, *args, **opts):
        if opts["dry_run"] and opts["reset"]:
            raise CommandError("--dry-run cannot be combined with --reset (a reset deletes data).")
        if not 10 <= opts["batch_size"] <= 5000:
            raise CommandError("--batch-size must be between 10 and 5000.")
        self._started = monotonic()
        self._verbose = opts["verbosity"] >= 2
        self._batch = opts["batch_size"]
        measure = opts["dry_run"] or self._verbose
        statements, sent = [0], [0]

        def count(execute, sql, params, many, context):
            result = execute(sql, params, many, context)
            statements[0] += 1
            if measure:         # bytes actually sent, as the driver composed them
                sent[0] += len((connection.ops.last_executed_query(context["cursor"], sql, params) or sql).encode())
            return result

        try:
            # ONE transaction for the whole seed: any error, and --dry-run itself, rolls back everything.
            with connection.execute_wrapper(count), transaction.atomic():
                self._seed(opts)
                if opts["dry_run"]:
                    raise _DryRunRollback
        except _DryRunRollback:
            # The with-block has exited by an exception, so the transaction is already rolled back.
            if Company.objects.filter(name=opts["company"]).exists():
                raise CommandError("DRY RUN FAILED: the demo company is still present after the rollback.")
            self.stdout.write(self.style.WARNING("DRY RUN: all of the above was rolled back; nothing was saved."))
        if opts["dry_run"] or self._verbose:
            self.stdout.write(
                f"  Diagnostics: about {statements[0]} SQL statements, about {sent[0] / 1024:.0f} KB sent, "
                f"{monotonic() - self._started:.1f}s (diagnostic only; it does not prove the data is correct).")

    def _stage(self, text):
        if self._verbose:
            self.stdout.write(f"  [{monotonic() - self._started:5.1f}s] {text}")
            self.stdout.flush()

    def _bulk(self, model, objs):
        """INSERT in chunks (one statement each), reporting progress. Objects get their primary keys."""
        for start in range(0, len(objs), self._batch):
            model.objects.bulk_create(objs[start:start + self._batch], batch_size=self._batch)
            self._stage(f"{model._meta.model_name}: {min(start + self._batch, len(objs))}/{len(objs)} rows")
        return objs

    def _seed(self, opts):
        name = opts["company"]
        if not name.startswith("Demo"):
            raise CommandError("Company name must start with 'Demo' so real companies can never be touched.")
        if not 8 <= opts["buyers"] <= 5000:
            raise CommandError("--buyers must be between 8 and 5000.")

        existing = Company.objects.filter(name=name)
        if existing.exists():
            if not opts["reset"]:
                raise CommandError(f"'{name}' already exists. Use --reset to replace it.")
            if not (settings.DEBUG or opts["force"]):
                raise CommandError("--reset is refused when DEBUG is off. Add --force only on a demo server.")
            # Sales/plots/types are PROTECTed on purpose (a real company must never be deleted by a
            # cascade), so remove the demo data in dependency order: obligations, sales, then the company.
            for old in existing:
                Obligation.objects.filter(company=old).delete()
                Sale.objects.filter(company=old).delete()
                old.delete()

        rng = random.Random(opts["seed"])
        today = timezone.localdate()
        password = opts["password"] or get_random_string(14)

        company = Company.objects.create(name=name)
        # Usernames are unique across the whole system, so they carry the company name.
        suffix = slugify(name).replace("-", "")
        departments = {n: Department.objects.create(company=company, name=n) for n in scenarios.DEPARTMENTS}

        def make_user(base, role, dept=None):
            username = f"{base}_{suffix}"
            return User.objects.create_user(
                username, password=password, company=company, role=role,
                department=departments.get(dept), first_name=names.DEMO_USERS[base][0],
                last_name=names.DEMO_USERS[base][1],
            )

        make_user("md", "md")
        make_user("manager", "manager")
        make_user("sales", "sales")
        make_user("viewer", "viewer")
        staff = {
            departments["Documentation"].id: make_user("docs", "staff", "Documentation"),
            departments["Survey"].id: make_user("survey", "staff", "Survey"),
            departments["Legal"].id: make_user("legal", "staff", "Legal"),
        }

        types = [
            ObligationType.objects.create(
                company=company, name=oname, trigger=trigger, sla_days=sla, default_department=departments[dept])
            for oname, trigger, sla, dept in scenarios.OBLIGATION_TYPES
        ]

        estates = []
        for ename, location, prefix, price_range in names.ESTATES:
            estates.append((Estate.objects.create(company=company, name=ename, location=location), prefix, price_range))
        self._stage("company, users, types and estates created")

        # Plots: buyers + about 10% unsold plots per estate.
        n_buyers = opts["buyers"]
        per_estate = [n_buyers // len(estates) + (1 if i < n_buyers % len(estates) else 0) for i in range(len(estates))]
        plots = {}
        for (estate, prefix, _), count in zip(estates, per_estate):
            total = count + max(2, count // 10)
            plots[estate.id] = self._bulk(
                Plot, [Plot(company=company, estate=estate, plot_number=f"{prefix}-{i:03d}") for i in range(1, total + 1)])

        # ---- sales, payments, sale history, obligations: drawn in memory, written in batches ----
        # The random draws are made in EXACTLY the order the one-row-at-a-time version made them, so the same
        # seed gives the same data.
        mix_names = [m for m, _ in scenarios.PAYMENT_MIX]
        mix_weights = [w for _, w in scenarios.PAYMENT_MIX]
        buyers, sales = [], []
        for i in range(n_buyers):
            estate, _, (low, high) = estates[i % len(estates)]
            plot = plots[estate.id].pop(0)
            buyers.append(Buyer(
                company=company,
                full_name=f"{rng.choice(names.FIRST_NAMES)} {rng.choice(names.LAST_NAMES)}",
            ))
            price = Decimal(rng.randrange(low, high + 1, 250_000))
            kind = rng.choices(mix_names, mix_weights)[0]
            if kind == "partial":
                pct = rng.choice([20, 30, 40, 50, 60, 70, 80])
                paid = Decimal(int(price) * pct // 100 // 10_000 * 10_000)
                sale_date, paid_on = today - timedelta(days=rng.randint(10, 300)), None
            else:
                lo, hi = {"recent": (0, 45), "mid": (46, 200), "old": (201, 600)}[kind]
                paid_on = today - timedelta(days=rng.randint(lo, hi))
                sale_date = paid_on - timedelta(days=rng.randint(30, 400))
                paid = price
            sale = Sale(company=company, plot=plot, price=price, amount_paid=paid, sale_date=sale_date)
            if paid >= sale.price:                      # same rule as services.create_sale
                sale.fully_paid_on = paid_on or sale_date
            sales.append((sale, paid_on))

        self._bulk(Buyer, buyers)
        for buyer, (sale, _) in zip(buyers, sales):
            sale.buyer = buyer
        self._bulk(Sale, [s for s, _ in sales])
        # Opening balance: the ledger equals the stored total from the very first day (as in create_sale).
        self._bulk(Payment, [
            Payment(company=company, sale=s, kind=Payment.Kind.OPENING, amount=s.amount_paid,
                    paid_on=paid_on or s.sale_date, note="Opening balance", recorded_by=None)
            for s, paid_on in sales if s.amount_paid > 0
        ])
        self._bulk(SaleEvent, [
            SaleEvent(company=company, sale=s, actor=None, action=SaleEvent.Action.CREATED,
                      changes={"price": [None, f"{s.price:.2f}"], "amount_paid": [None, f"{s.amount_paid:.2f}"]})
            for s, _ in sales
        ])

        # The obligations each sale is entitled to (same rules as services.generate_obligations). Built in
        # (sale, type) order, which is the order the progress step below must walk them in.
        obligations = []
        for s, _ in sales:
            if s.status != Sale.Status.ACTIVE:
                continue
            for t in types:
                if not t.is_active:
                    continue
                if t.trigger == ObligationType.Trigger.ON_FULL_PAYMENT:
                    if s.fully_paid_on is None:
                        continue
                    start = s.fully_paid_on
                else:
                    start = s.sale_date
                obligations.append(Obligation(
                    company=company, sale=s, obligation_type=t, started_on=start,
                    due_date=start + timedelta(days=t.sla_days), department=t.default_department))

        # ---- progress each obligation IN MEMORY, so each row is INSERTed once, in its final state ----
        # (Writing it first and correcting it afterwards would send every obligation twice.)
        progressed = []                      # (obligation, backdated time, its event or None), in walk order
        for ob in obligations:
            touch_dt, event = self.progress(company, ob, rng, today, staff)
            ob.last_updated_at = touch_dt
            progressed.append((ob, touch_dt, event))
        self._bulk(Obligation, obligations)
        self._stage(f"{len(sales)} sales, payments, sale history and {len(obligations)} obligations written")

        # ---- obligation history: creation events first (ids ascend per obligation, as with the service),
        # then progress events, each already carrying its backdated created_at ----
        events = []
        for ob, _, _ in progressed:
            events.append(ObligationEvent(company=company, obligation=ob, note="Created automatically",
                                          created_at=at(ob.started_on)))
        for _, touch_dt, event in progressed:
            if event is not None:
                event.created_at = touch_dt
                events.append(event)
        with explicit_created_at(ObligationEvent):
            self._bulk(ObligationEvent, events)
        self._stage(f"{len(events)} obligation events written")

        verb = "would be created (dry run)" if opts["dry_run"] else "created"
        self.stdout.write(self.style.SUCCESS(f"Demo company '{name}' {verb} with {n_buyers} buyers."))
        self.stdout.write(f"  Obligations: {len(obligations)}")
        self.stdout.write(f"  Exposure: {services.exposure(company)}")
        self.stdout.write(f"  Ageing: {services.ageing(company)}")
        self.stdout.write(f"  Overdue: {services.overdue(company).count()}   Stale (14+ days): {services.stale(company).count()}")
        logins = ", ".join(f"{b}_{suffix}" for b in ("md", "manager", "docs", "survey", "legal", "sales", "viewer"))
        self.stdout.write(f"  Demo logins: {logins}")
        if opts["password"] is None and not opts["dry_run"]:
            self.stdout.write(f"  Password (shown once): {password}")

    # ---- helpers ----

    def apply_update(self, company, ob, actor, note, changes):
        """In-memory twin of services.update_obligation: the same guards, the same before/after diff over the
        same tracked fields, the same completed_on rule. Returns the ObligationEvent to write (or None when
        nothing changed). tracker/test_seed.py compares the result with the real service."""
        if actor.company_id != ob.company_id:
            raise PermissionDenied("Obligation belongs to another company.")
        if ob.sale.status != Sale.Status.ACTIVE:
            raise services.SaleStateError("This sale is cancelled, so its obligations are read-only.")
        diff = {}
        for field, new in changes.items():
            if field not in services.TRACKED_FIELDS:
                raise ValueError(f"Field not editable: {field}")
            old = getattr(ob, field)
            if old != new:
                diff[field] = [services._ser(old), services._ser(new)]
                setattr(ob, field, new)
        if not diff and not note:
            return None
        if ob.status == Obligation.Status.COMPLETED:
            ob.completed_on = ob.completed_on or timezone.localdate()
        else:
            ob.completed_on = None
        return ObligationEvent(company=company, obligation=ob, actor=actor, changes=diff, note=note)

    def progress(self, company, ob, rng, today, staff):
        """Move one obligation to a realistic state (in memory, through apply_update), and return
        (its 'last touched' time, its event or None). Seeding is the only place that backdates."""
        name = ob.obligation_type.name
        age = (today - ob.started_on).days
        actor = staff[ob.department_id]
        touch_dt = at(ob.started_on)
        event = None

        p_done = scenarios.COMPLETION_CHANCE[name] * (0.4 if age < 30 else 1.0)
        if rng.random() < p_done:
            lo = min(5, age)
            hi = max(lo, min(age, ob.obligation_type.sla_days + 45))
            touch_day = ob.started_on + timedelta(days=rng.randint(lo, hi))
            event = self.apply_update(company, ob, actor, "Delivered to buyer", dict(
                owner=actor, status="completed", stage="Delivered to buyer"))
            touch_dt, ob.completed_on = at(touch_day, 15), touch_day
        else:
            r = rng.random()
            if r < 0.35 or r < 0.70:
                external = r < 0.35
                r2 = rng.random()
                days_ago = rng.randint(0, 7) if r2 < 0.45 else rng.randint(8, 14) if r2 < 0.70 else rng.randint(15, 60)
                touch_day = today - timedelta(days=min(days_ago, age))
                if external:
                    party = rng.choice(scenarios.EXTERNAL_PARTIES[name])
                    event = self.apply_update(company, ob, actor, "Submitted externally", dict(
                        owner=actor, status="awaiting_external", stage=f"With {party}",
                        external_party=party, reason=rng.choice(scenarios.EXTERNAL_REASONS),
                        next_action=rng.choice(scenarios.NEXT_ACTIONS_EXTERNAL),
                        next_review_date=today + timedelta(days=rng.randint(-10, 21))))
                else:
                    event = self.apply_update(company, ob, actor, "Work started", dict(
                        owner=actor, status="in_progress", stage=rng.choice(scenarios.INTERNAL_STAGES),
                        reason=rng.choice(scenarios.INTERNAL_REASONS),
                        next_action=rng.choice(scenarios.NEXT_ACTIONS_INTERNAL),
                        next_review_date=today + timedelta(days=rng.randint(-5, 14))))
                touch_dt = at(touch_day, 15)
            # else: untouched (no owner, no next action): stays "incomplete" and goes stale on its own
        return touch_dt, event
