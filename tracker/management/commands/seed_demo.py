import random
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import get_random_string
from django.utils.text import slugify

from tracker import services
from tracker.models import (
    Buyer, Company, Department, Estate, Obligation, ObligationEvent, ObligationType, Plot, Sale, User,
)
from tracker.seed import names, scenarios


def at(day, hour=9):
    """A timezone-aware datetime on the given local date, never later than 'now'.
    (Without the clamp, seeding in the morning would create 'last updated' times later today,
    i.e. in the future.)"""
    moment = timezone.make_aware(datetime.combine(day, time(hour, 0)))
    return min(moment, timezone.now())


class Command(BaseCommand):
    help = (
        "Create a synthetic DEMO company (never real data). Everything is built through the same "
        "services the app uses, so history and rules are real."
    )

    def add_arguments(self, parser):
        parser.add_argument("--company", default="Demo Homes", help="Must start with 'Demo'.")
        parser.add_argument("--buyers", type=int, default=300)
        parser.add_argument("--seed", type=int, default=42, help="Same seed gives the same data.")
        parser.add_argument("--password", default=None, help="Password for demo users (random if omitted).")
        parser.add_argument("--reset", action="store_true", help="Delete the existing demo company first.")
        parser.add_argument("--force", action="store_true", help="Allow --reset when DEBUG is off (demo servers only).")

    @transaction.atomic
    def handle(self, *args, **opts):
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
                department=departments.get(dept), first_name=base.title(),
            )

        make_user("md", "md")
        manager = make_user("manager", "manager")
        make_user("sales", "sales")
        make_user("viewer", "viewer")
        staff = {
            departments["Documentation"].id: make_user("docs", "staff", "Documentation"),
            departments["Survey"].id: make_user("survey", "staff", "Survey"),
            departments["Legal"].id: make_user("legal", "staff", "Legal"),
        }

        for oname, trigger, sla, dept in scenarios.OBLIGATION_TYPES:
            ObligationType.objects.create(
                company=company, name=oname, trigger=trigger, sla_days=sla, default_department=departments[dept])

        estates = []
        for ename, location, prefix, price_range in names.ESTATES:
            estates.append((Estate.objects.create(company=company, name=ename, location=location), prefix, price_range))

        # Plots: buyers + about 10% unsold plots per estate.
        n_buyers = opts["buyers"]
        per_estate = [n_buyers // len(estates) + (1 if i < n_buyers % len(estates) else 0) for i in range(len(estates))]
        plots = {}
        for (estate, prefix, _), count in zip(estates, per_estate):
            total = count + max(2, count // 10)
            plots[estate.id] = [
                Plot.objects.create(company=company, estate=estate, plot_number=f"{prefix}-{i:03d}")
                for i in range(1, total + 1)
            ]

        mix_names = [m for m, _ in scenarios.PAYMENT_MIX]
        mix_weights = [w for _, w in scenarios.PAYMENT_MIX]

        for i in range(n_buyers):
            estate, _, (low, high) = estates[i % len(estates)]
            plot = plots[estate.id].pop(0)
            buyer = Buyer.objects.create(
                company=company,
                full_name=f"{rng.choice(names.FIRST_NAMES)} {rng.choice(names.LAST_NAMES)}",
            )
            price = Decimal(rng.randrange(low, high + 1, 250_000))
            kind = rng.choices(mix_names, mix_weights)[0]
            if kind == "partial":
                pct = rng.choice([20, 30, 40, 50, 60, 70, 80])
                paid = Decimal(int(price) * pct // 100 // 10_000 * 10_000)
                services.create_sale(company, plot, buyer, price, today - timedelta(days=rng.randint(10, 300)), paid)
            else:
                lo, hi = {"recent": (0, 45), "mid": (46, 200), "old": (201, 600)}[kind]
                paid_on = today - timedelta(days=rng.randint(lo, hi))
                sale_date = paid_on - timedelta(days=rng.randint(30, 400))
                services.create_sale(company, plot, buyer, price, sale_date, price, paid_on)

        obligations = Obligation.objects.filter(company=company).select_related("obligation_type", "sale")
        for ob in obligations:
            self.progress(ob, rng, today, staff)

        self.stdout.write(self.style.SUCCESS(f"Demo company '{name}' created with {n_buyers} buyers."))
        self.stdout.write(f"  Obligations: {obligations.count()}")
        self.stdout.write(f"  Exposure: {services.exposure(company)}")
        self.stdout.write(f"  Ageing: {services.ageing(company)}")
        self.stdout.write(f"  Overdue: {services.overdue(company).count()}   Stale (14+ days): {services.stale(company).count()}")
        logins = ", ".join(f"{b}_{suffix}" for b in ("md", "manager", "docs", "survey", "legal", "sales", "viewer"))
        self.stdout.write(f"  Demo logins: {logins}")
        if opts["password"] is None:
            self.stdout.write(f"  Password (shown once): {password}")

    # ---- helpers ----

    def progress(self, ob, rng, today, staff):
        """Move one obligation to a realistic state using the real update service, then make the
        timestamps consistent with its age (seeding is the only place that backdates)."""
        name = ob.obligation_type.name
        age = (today - ob.started_on).days
        actor = staff[ob.department_id]
        first_event = ob.events.order_by("created_at", "id").first()
        touch_dt = at(ob.started_on)
        completed_on = None

        p_done = scenarios.COMPLETION_CHANCE[name] * (0.4 if age < 30 else 1.0)
        if rng.random() < p_done:
            lo = min(5, age)
            hi = max(lo, min(age, ob.obligation_type.sla_days + 45))
            touch_day = ob.started_on + timedelta(days=rng.randint(lo, hi))
            services.update_obligation(ob, actor, owner=actor, status="completed",
                                       stage="Delivered to buyer", note="Delivered to buyer")
            touch_dt, completed_on = at(touch_day, 15), touch_day
        else:
            r = rng.random()
            if r < 0.35 or r < 0.70:
                external = r < 0.35
                r2 = rng.random()
                days_ago = rng.randint(0, 7) if r2 < 0.45 else rng.randint(8, 14) if r2 < 0.70 else rng.randint(15, 60)
                touch_day = today - timedelta(days=min(days_ago, age))
                if external:
                    party = rng.choice(scenarios.EXTERNAL_PARTIES[name])
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
                touch_dt = at(touch_day, 15)
            # else: untouched (no owner, no next action): stays "incomplete" and goes stale on its own

        # Consistent timestamps: creation event at the start date, later events and "last updated" at the touch time.
        ObligationEvent.objects.filter(pk=first_event.pk).update(created_at=at(ob.started_on))
        ObligationEvent.objects.filter(obligation=ob).exclude(pk=first_event.pk).update(created_at=touch_dt)
        changes = {"last_updated_at": touch_dt}
        if completed_on:
            changes["completed_on"] = completed_on
        Obligation.objects.filter(pk=ob.pk).update(**changes)
