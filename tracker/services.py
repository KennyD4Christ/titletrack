from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone

from .models import Obligation, ObligationEvent, ObligationType, Sale

TRACKED_FIELDS = (
    "status", "stage", "owner", "department", "external_party",
    "reason", "next_action", "next_review_date",
)


def _ser(value):
    if value is None:
        return None
    if hasattr(value, "pk"):
        return value.pk
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


# ---------- writes ----------

@transaction.atomic
def generate_obligations(sale):
    """Create any obligations this sale is now entitled to. Safe to call repeatedly."""
    created = []
    if sale.status != Sale.Status.ACTIVE:
        return created
    for t in ObligationType.objects.filter(company=sale.company, is_active=True):
        if t.trigger == ObligationType.Trigger.ON_FULL_PAYMENT:
            if sale.fully_paid_on is None:
                continue
            start = sale.fully_paid_on
        else:
            start = sale.sale_date
        obj, was_created = Obligation.objects.get_or_create(
            sale=sale,
            obligation_type=t,
            defaults=dict(
                company=sale.company,
                started_on=start,
                due_date=start + timedelta(days=t.sla_days),
                department=t.default_department,
            ),
        )
        if was_created:
            ObligationEvent.objects.create(company=sale.company, obligation=obj, note="Created automatically")
            created.append(obj)
    return created


@transaction.atomic
def create_sale(company, plot, buyer, price, sale_date, amount_paid=0, paid_on=None):
    amount_paid = Decimal(amount_paid)
    sale = Sale(
        company=company, plot=plot, buyer=buyer, price=Decimal(price),
        amount_paid=amount_paid, sale_date=sale_date,
    )
    if amount_paid >= sale.price:
        sale.fully_paid_on = paid_on or sale_date
    sale.save()
    generate_obligations(sale)
    return sale


@transaction.atomic
def register_payment_total(sale, amount_paid, paid_on=None):
    """Set the total paid to date (overlay model: no payment ledger in the MVP)."""
    sale = Sale.objects.select_for_update().get(pk=sale.pk)
    sale.amount_paid = Decimal(amount_paid)
    if sale.amount_paid >= sale.price and sale.fully_paid_on is None:
        sale.fully_paid_on = paid_on or timezone.localdate()
    sale.save()
    generate_obligations(sale)
    return sale


@transaction.atomic
def update_obligation(obligation, actor, note="", **changes):
    """The ONLY way obligations should be edited. Logs before/after and refreshes last_updated_at."""
    obligation = Obligation.objects.select_for_update().get(pk=obligation.pk)
    if actor.company_id != obligation.company_id:
        raise PermissionDenied("Obligation belongs to another company.")
    diff = {}
    for field, new in changes.items():
        if field not in TRACKED_FIELDS:
            raise ValueError(f"Field not editable: {field}")
        old = getattr(obligation, field)
        if old != new:
            diff[field] = [_ser(old), _ser(new)]
            setattr(obligation, field, new)
    if not diff and not note:
        return obligation
    if obligation.status == Obligation.Status.COMPLETED:
        obligation.completed_on = obligation.completed_on or timezone.localdate()
    else:
        obligation.completed_on = None
    obligation.last_updated_at = timezone.now()
    obligation.save()
    ObligationEvent.objects.create(
        company=obligation.company, obligation=obligation, actor=actor, changes=diff, note=note
    )
    return obligation


# ---------- reads (all scoped by company) ----------

def open_obligations(company):
    return Obligation.objects.filter(company=company, status__in=Obligation.OPEN_STATUSES)


def exposure(company, fully_paid_only=False):
    """Money collected on active sales that still have at least one open obligation.
    Each sale is counted once, however many obligations are open on it."""
    sales = Sale.objects.filter(
        company=company,
        status=Sale.Status.ACTIVE,
        id__in=open_obligations(company).values("sale_id"),
    )
    if fully_paid_only:
        sales = sales.filter(fully_paid_on__isnull=False)
    agg = sales.aggregate(amount=Sum("amount_paid"), buyers=Count("buyer", distinct=True), sales=Count("id"))
    return {"amount": agg["amount"] or Decimal("0"), "buyers": agg["buyers"], "sales": agg["sales"]}


def ageing(company, today=None):
    """Open obligations bucketed by days since they started (date-range filters: DB-agnostic)."""
    today = today or timezone.localdate()
    d30, d60, d90 = (today - timedelta(days=n) for n in (30, 60, 90))
    return open_obligations(company).aggregate(
        d0_30=Count("id", filter=Q(started_on__gte=d30)),
        d31_60=Count("id", filter=Q(started_on__lt=d30, started_on__gte=d60)),
        d61_90=Count("id", filter=Q(started_on__lt=d60, started_on__gte=d90)),
        d90_plus=Count("id", filter=Q(started_on__lt=d90)),
    )


def overdue(company, today=None):
    today = today or timezone.localdate()
    return open_obligations(company).filter(due_date__lt=today)


def stale(company, days=14, now=None):
    now = now or timezone.now()
    return open_obligations(company).filter(last_updated_at__lt=now - timedelta(days=days))


def my_queue(user, today=None):
    today = today or timezone.localdate()
    return (
        open_obligations(user.company)
        .filter(owner=user)
        .select_related("sale__buyer", "sale__plot__estate", "obligation_type")
        .order_by("due_date", "id")
    )


def who_has_the_ball(company, today=None):
    today = today or timezone.localdate()
    return (
        open_obligations(company)
        .values("department__name", "owner__username")
        .annotate(open=Count("id"), late=Count("id", filter=Q(due_date__lt=today)))
        .order_by("-late", "-open")
    )
