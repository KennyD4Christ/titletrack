"""Read-only dashboard queries. Every function takes a company and counts ACTIVE WORK only
(open obligations on active sales: see services.open_obligations). Pure reads, no writes."""
import re
from collections import Counter, defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Min, Q, Sum
from django.db.models.functions import Trim
from django.utils import timezone

from . import computed, services
from .models import Obligation, ObligationType, Sale

DUE_SOON_DAYS = 7
AWAITING = Obligation.Status.AWAITING_EXTERNAL


def _now_today(today=None, now=None):
    return (today or timezone.localdate()), (now or timezone.now())


def attention_summary(company, today=None, now=None):
    """The 'what needs attention' counts. Each one has a matching filter on /api/obligations/."""
    today, now = _now_today(today, now)
    open_qs = services.open_obligations(company)
    cutoff = now - timedelta(days=computed.STALE_RED_DAYS)
    out = open_qs.aggregate(
        open=Count("id"),
        overdue=Count("id", filter=Q(due_date__lt=today)),
        due_soon=Count("id", filter=Q(due_date__gte=today, due_date__lte=today + timedelta(days=DUE_SOON_DAYS))),
        stale=Count("id", filter=Q(last_updated_at__lt=cutoff)),
        awaiting_external=Count("id", filter=Q(status=AWAITING)),
    )
    out["incomplete"] = (open_qs.annotate(_na=Trim("next_action"))
                         .filter(Q(owner__isnull=True) | Q(_na="")).count())
    return out


def average_wait_days(company, today=None):
    today, _ = _now_today(today)
    starts = list(services.open_obligations(company).values_list("started_on", flat=True))
    if not starts:
        return None
    return round(sum((today - d).days for d in starts) / len(starts), 1)


def exposure_summary(company, today=None):
    total = services.exposure(company)
    paid = services.exposure(company, fully_paid_only=True)
    return {"amount": total["amount"], "buyers": total["buyers"], "sales": total["sales"],
            "fully_paid_amount": paid["amount"], "fully_paid_buyers": paid["buyers"],
            "average_wait_days": average_wait_days(company, today)}


def by_estate(company, today=None):
    today, _ = _now_today(today)
    open_qs = services.open_obligations(company)
    money = (Sale.objects.filter(company=company, status=Sale.Status.ACTIVE, id__in=open_qs.values("sale_id"))
             .values("plot__estate_id", "plot__estate__name")
             .annotate(amount=Sum("amount_paid"), sales=Count("id"), buyers=Count("buyer", distinct=True)))
    work = {r["sale__plot__estate_id"]: r for r in open_qs.values("sale__plot__estate_id").annotate(
        open_obligations=Count("id"), overdue=Count("id", filter=Q(due_date__lt=today)), oldest=Min("started_on"))}
    rows = []
    for r in money:
        w = work.get(r["plot__estate_id"], {})
        rows.append({
            "estate_id": r["plot__estate_id"], "estate": r["plot__estate__name"], "amount": r["amount"] or Decimal("0"),
            "buyers": r["buyers"], "sales": r["sales"], "open_obligations": w.get("open_obligations", 0),
            "overdue": w.get("overdue", 0),
            "oldest_days": (today - w["oldest"]).days if w.get("oldest") else None,
        })
    return sorted(rows, key=lambda r: (-r["amount"], r["estate"]))


def by_type(company, today=None):
    """Per document type. 'amount' is NOT additive across types: one buyer can wait on several documents."""
    today, _ = _now_today(today)
    open_qs = services.open_obligations(company)
    rows = []
    for t in ObligationType.objects.filter(company=company, is_active=True).order_by("name"):
        of_type = open_qs.filter(obligation_type=t)
        agg = of_type.aggregate(open=Count("id"), overdue=Count("id", filter=Q(due_date__lt=today)),
                                oldest=Min("started_on"))
        sales = Sale.objects.filter(id__in=of_type.values("sale_id"))
        money = sales.aggregate(amount=Sum("amount_paid"), buyers=Count("buyer", distinct=True))
        rows.append({
            "type_id": t.id, "type": t.name, "open": agg["open"], "overdue": agg["overdue"],
            "buyers": money["buyers"], "amount": money["amount"] or Decimal("0"),
            "oldest_days": (today - agg["oldest"]).days if agg["oldest"] else None,
        })
    return sorted(rows, key=lambda r: (-r["open"], r["type"]))


def delay_reasons(company, limit=8):
    """Most common reasons written on open obligations (case and spacing ignored)."""
    counter, display = Counter(), {}
    for text in (
        services.open_obligations(company)
        .exclude(reason="")
        .order_by("id")
        .values_list("reason", flat=True)
    ):
        clean = re.sub(r"\s+", " ", text).strip()
        if not clean:
            continue
        key = clean.casefold()
        counter[key] += 1
        display.setdefault(key, clean)
    return [{"reason": display[k], "count": n} for k, n in counter.most_common(limit)]


def oldest_cases(company, limit=10):
    return (services.open_obligations(company)
            .select_related("sale__buyer", "sale__plot__estate", "obligation_type", "owner", "department")
            .order_by("started_on", "id")[:limit])


def _owner_name(first, last, username):
    return (f"{first} {last}".strip()) or username


def workload(company, today=None, now=None):
    """Who holds the ball: by department and by owner."""
    today, now = _now_today(today, now)
    cutoff = now - timedelta(days=computed.STALE_RED_DAYS)
    open_qs = services.open_obligations(company)
    counts = dict(open=Count("id"), overdue=Count("id", filter=Q(due_date__lt=today)),
                  stale=Count("id", filter=Q(last_updated_at__lt=cutoff)),
                  awaiting_external=Count("id", filter=Q(status=AWAITING)))
    departments = [
        {"department": r["department__name"] or "Unassigned", **{k: r[k] for k in counts},
         "unowned": r["unowned"]}
        for r in open_qs.values("department__name").annotate(**counts, unowned=Count("id", filter=Q(owner__isnull=True)))
    ]
    by_owner = defaultdict(lambda: {"open": 0, "overdue": 0, "stale": 0, "awaiting_external": 0, "departments": set()})
    for r in open_qs.filter(owner__isnull=False).values(
            "owner_id", "owner__first_name", "owner__last_name", "owner__username", "department__name").annotate(**counts):
        row = by_owner[r["owner_id"]]
        row["owner_id"] = r["owner_id"]
        row["owner"] = _owner_name(r["owner__first_name"], r["owner__last_name"], r["owner__username"])
        for k in counts:
            row[k] += r[k]
        row["departments"].add(r["department__name"] or "Unassigned")
    owners = sorted(({**v, "departments": sorted(v["departments"])} for v in by_owner.values()),
                    key=lambda r: (-r["overdue"], -r["open"], r["owner"]))
    departments.sort(key=lambda r: (-r["overdue"], -r["open"], r["department"]))
    return {"by_department": departments, "by_owner": owners}


def cancelled_recently(company, days=30, now=None):
    _, now = _now_today(None, now)
    return Sale.objects.filter(company=company, status=Sale.Status.CANCELLED,
                               cancelled_at__gte=now - timedelta(days=days)).count()


def queue_groups(user, today=None):
    """The signed-in person's own active work, split into the groups the My Queue screen shows."""
    today, _ = _now_today(today)
    items = list(services.my_queue(user))
    soon = today + timedelta(days=DUE_SOON_DAYS)
    groups = {"overdue": [], "due_soon": [], "awaiting_external": [], "other": []}
    for ob in items:
        if ob.due_date and ob.due_date < today:
            groups["overdue"].append(ob)
        elif ob.due_date and ob.due_date <= soon:
            groups["due_soon"].append(ob)
        elif ob.status == AWAITING:
            groups["awaiting_external"].append(ob)
        else:
            groups["other"].append(ob)
    return groups
