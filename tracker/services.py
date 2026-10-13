import re
from dataclasses import dataclass, field as dc_field
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone

from .importing import clean_amount, clean_date
from .models import (
    Buyer, Estate, Obligation, ObligationEvent, ObligationType, Payment, Plot, Sale, SaleEvent,
)


class SaleStateError(Exception):
    """The sale is in the wrong state for this action (for example: editing a cancelled sale's obligations)."""


class PlotOccupied(SaleStateError):
    """The plot already has an active sale. `.sale` is that sale (buyer, plot and estate are loaded)."""

    def __init__(self, sale):
        self.sale = sale
        super().__init__(
            f"Plot {sale.plot.plot_number} in {sale.plot.estate.name} already has an active sale "
            f"to {sale.buyer.full_name}.")


TRACKED_FIELDS = (
    "status", "stage", "owner", "department", "external_party",
    "reason", "next_action", "next_review_date",
)

CENT = Decimal("0.01")
MAX_AMOUNT = Decimal(10) ** 12
EARLIEST_YEAR = 1990


def _ser(value):
    if value is None:
        return None
    if hasattr(value, "pk"):
        return value.pk
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


# ---------- input checks (same limits as the spreadsheet import) ----------

def _money(value, label):
    """A positive or negative amount with at most two decimals, below 10^12. Raises ValueError."""
    try:
        amount = clean_amount(value)
    except ValueError:
        raise ValueError(f"{label} is not a valid amount.") from None
    if not amount.is_finite():
        raise ValueError(f"{label} is not a valid amount.")
    if abs(amount) >= MAX_AMOUNT:
        raise ValueError(f"{label} is too large.")
    if amount != amount.quantize(CENT):
        raise ValueError(f"{label} can have at most two decimal places.")
    return amount.quantize(CENT)


def _checked_date(value, label):
    try:
        day = clean_date(value)
    except ValueError:
        raise ValueError(f"{label} is not a valid date.") from None
    if day.year < EARLIEST_YEAR or day > timezone.localdate() + timedelta(days=1):
        raise ValueError(f"{label} is implausible (before {EARLIEST_YEAR} or in the future).")
    return day


def _clean_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _key(value):
    return _clean_text(value).casefold()


def _required_text(value, label, limit):
    text = _clean_text(value)
    if not text:
        raise ValueError(f"{label} is required.")
    if len(text) > limit:
        raise ValueError(f"{label} is too long (maximum {limit} characters).")
    return text


def _log_sale(sale, actor, action, changes=None, note=""):
    return SaleEvent.objects.create(
        company_id=sale.company_id, sale=sale, actor=actor, action=action, changes=changes or {}, note=note)


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
def create_sale(company, plot, buyer, price, sale_date, amount_paid=0, paid_on=None, actor=None):
    amount_paid = Decimal(amount_paid)
    sale = Sale(
        company=company, plot=plot, buyer=buyer, price=Decimal(price),
        amount_paid=amount_paid, sale_date=sale_date,
    )
    if amount_paid >= sale.price:
        sale.fully_paid_on = paid_on or sale_date
    sale.save()
    if amount_paid > 0:
        # The opening balance keeps the ledger equal to the stored total from the very first day.
        Payment.objects.create(
            company=company, sale=sale, kind=Payment.Kind.OPENING, amount=amount_paid,
            paid_on=paid_on or sale_date, note="Opening balance", recorded_by=actor)
    _log_sale(sale, actor, SaleEvent.Action.CREATED,
              {"price": [None, f"{sale.price:.2f}"],
               "amount_paid": [None, f"{amount_paid:.2f}"]})
    generate_obligations(sale)
    return sale


@transaction.atomic
def register_payment_total(sale, amount_paid, paid_on=None):
    """Set the total paid to date directly (the original MVP path, kept for compatibility).
    Any difference is written to the ledger as an 'adjustment' so the ledger still adds up.
    New code should use record_payment."""
    sale = Sale.objects.select_for_update().get(pk=sale.pk)
    new_total = Decimal(amount_paid)
    difference = new_total - sale.amount_paid
    sale.amount_paid = new_total
    if sale.amount_paid >= sale.price and sale.fully_paid_on is None:
        sale.fully_paid_on = paid_on or timezone.localdate()
    sale.save()
    if difference != 0:
        Payment.objects.create(
            company_id=sale.company_id, sale=sale, kind=Payment.Kind.ADJUSTMENT, amount=difference,
            paid_on=paid_on or timezone.localdate(), note="Total set directly")
    generate_obligations(sale)
    return sale


@dataclass
class PaymentResult:
    payment: Payment
    sale: Sale
    obligations_created: list = dc_field(default_factory=list)
    duplicate: bool = False        # True when the request key had already been used: nothing new was recorded


@transaction.atomic
def record_payment(sale, actor, amount, paid_on=None, reference="", note="", idempotency_key=""):
    """Record one payment against an active sale.

    - Locks the sale row, so concurrent requests are handled one at a time.
    - The same idempotency key for the same sale records the payment once and returns the original.
    - A payment that would take the total above the price is refused.
    - When the payment completes the price, the obligations the sale is now entitled to are created
      exactly once (generate_obligations is idempotent) and returned in `obligations_created`.
    Raises ValueError (bad input), SaleStateError (cancelled sale), PermissionDenied (other company)."""
    amount = _money(amount, "Amount")
    if amount <= 0:
        raise ValueError("The payment amount must be greater than zero.")
    explicit_date = paid_on is not None and paid_on != ""
    paid_on = _checked_date(paid_on, "Payment date") if explicit_date else timezone.localdate()
    reference = _clean_text(reference)
    if len(reference) > 120:
        raise ValueError("Reference is too long (maximum 120 characters).")
    note = (note or "").strip()
    key = (idempotency_key or "").strip()
    if len(key) > 64:
        raise ValueError("The request key is too long (maximum 64 characters).")

    locked = Sale.objects.select_for_update().get(pk=sale.pk)
    if actor.company_id != locked.company_id:
        raise PermissionDenied("Sale belongs to another company.")

    if key:
        previous = Payment.objects.filter(sale=locked, idempotency_key=key).first()
        if previous is not None:
            if previous.amount != amount or (explicit_date and previous.paid_on != paid_on):
                raise ValueError("This request key was already used for a different payment.")
            return PaymentResult(previous, locked, [], duplicate=True)

    if locked.status != Sale.Status.ACTIVE:
        raise SaleStateError("This sale is cancelled, so payments cannot be recorded.")

    before = locked.amount_paid
    new_total = before + amount
    if new_total > locked.price:
        raise ValueError(
            f"This payment would take the total paid to {new_total}, which is more than the price "
            f"{locked.price}. The most that can still be recorded is {max(locked.price - before, Decimal('0'))}.")

    payment = Payment.objects.create(
        company_id=locked.company_id, sale=locked, kind=Payment.Kind.PAYMENT, amount=amount, paid_on=paid_on,
        reference=reference, note=note, recorded_by=actor, idempotency_key=key)
    locked.amount_paid = new_total
    changes = {"amount_paid": [str(before), str(new_total)]}
    if new_total >= locked.price and locked.fully_paid_on is None:
        locked.fully_paid_on = max(paid_on, locked.sale_date)
        changes["fully_paid_on"] = [None, locked.fully_paid_on.isoformat()]
    locked.save(update_fields=["amount_paid", "fully_paid_on"])
    _log_sale(locked, actor, SaleEvent.Action.PAYMENT, changes,
              f"Payment #{payment.pk}" + (f" ({reference})" if reference else "") + (f": {note}" if note else ""))
    created = generate_obligations(locked)
    return PaymentResult(payment, locked, created)


@transaction.atomic
def reverse_payment(payment, actor, reason):
    """Undo a payment with a NEW negative ledger entry that keeps the reason. The original is never changed.
    Obligations already created stay as they are (reversing a payment never deletes work)."""
    reason = (reason or "").strip()
    if len(reason) < 3:
        raise ValueError("A reason for the reversal is required.")
    locked_sale = Sale.objects.select_for_update().get(pk=payment.sale_id)
    original = Payment.objects.select_for_update().get(pk=payment.pk)
    if actor.company_id != locked_sale.company_id:
        raise PermissionDenied("Sale belongs to another company.")
    if original.kind == Payment.Kind.REVERSAL or original.amount <= 0:
        raise ValueError("This entry cannot be reversed.")
    if Payment.objects.filter(reverses=original).exists():
        raise SaleStateError("This payment has already been reversed.")
    if locked_sale.status != Sale.Status.ACTIVE:
        raise SaleStateError("This sale is cancelled, so payments cannot be reversed.")
    before = locked_sale.amount_paid
    new_total = before - original.amount
    if new_total < 0:
        raise ValueError("Reversing this payment would take the total paid below zero.")
    reversal = Payment.objects.create(
        company_id=locked_sale.company_id, sale=locked_sale, kind=Payment.Kind.REVERSAL, amount=-original.amount,
        paid_on=timezone.localdate(), reference=f"Reversal of payment #{original.pk}", note=reason,
        reverses=original, recorded_by=actor)
    locked_sale.amount_paid = new_total
    locked_sale.save(update_fields=["amount_paid"])
    _log_sale(locked_sale, actor, SaleEvent.Action.PAYMENT_REVERSED,
              {"amount_paid": [str(before), str(new_total)]}, reason)
    return reversal


def _active_sale_on(plot):
    return (Sale.objects.filter(plot=plot, status=Sale.Status.ACTIVE)
            .select_related("plot__estate", "buyer").first())


def _get_or_create_estate(company, name):
    for estate in Estate.objects.filter(company=company):
        if _key(estate.name) == _key(name):
            return estate
    try:
        with transaction.atomic():
            return Estate.objects.create(company=company, name=name)
    except IntegrityError:                                   # someone created it a moment ago
        for estate in Estate.objects.filter(company=company):
            if _key(estate.name) == _key(name):
                return estate
        raise


def _get_or_create_plot(company, estate, number):
    for plot in Plot.objects.filter(estate=estate):
        if _key(plot.plot_number) == _key(number):
            return plot
    try:
        with transaction.atomic():
            return Plot.objects.create(company=company, estate=estate, plot_number=number)
    except IntegrityError:
        for plot in Plot.objects.filter(estate=estate):
            if _key(plot.plot_number) == _key(number):
                return plot
        raise


@transaction.atomic
def add_sale(company, actor, *, plot_number, price, sale_date, estate=None, estate_name="", buyer=None,
             buyer_name="", phone="", email="", amount_paid=0, paid_on=None):
    """Direct entry of a new sale. Everything is checked first and written in one transaction, so a refusal
    leaves nothing behind (no stray estate, plot or buyer). A new buyer is ALWAYS a new person: buyers are
    never merged on name alone. To reuse a person, pass `buyer`.
    Raises ValueError (bad input), PlotOccupied (409-type), PermissionDenied (other company's records)."""
    if actor.company_id != company.pk:
        raise PermissionDenied("You belong to another company.")
    price = _money(price, "Price")
    if price <= 0:
        raise ValueError("Price must be greater than zero.")
    amount_paid = _money(amount_paid, "Amount paid")
    if amount_paid < 0:
        raise ValueError("Amount paid cannot be negative.")
    if amount_paid > price:
        raise ValueError("Amount paid cannot be more than the price.")
    sale_date = _checked_date(sale_date, "Sale date")
    if amount_paid > 0:
        paid_on = _checked_date(paid_on, "Payment date") if paid_on else sale_date
        if paid_on < sale_date:
            raise ValueError("Payment date cannot be before the sale date.")
    else:
        paid_on = None

    plot_number = _required_text(plot_number, "Plot number", 50)
    if estate is not None:
        if estate.company_id != company.pk:
            raise PermissionDenied("Estate belongs to another company.")
    else:
        estate_name = _required_text(estate_name, "Estate", 200)
    if buyer is not None:
        if buyer.company_id != company.pk:
            raise PermissionDenied("Buyer belongs to another company.")
    else:
        buyer_name = _required_text(buyer_name, "Buyer name", 200)
        phone = _clean_text(phone)
        if len(phone) > 40:
            raise ValueError("Phone is too long (maximum 40 characters).")
        email = (email or "").strip()
        if email:
            try:
                validate_email(email)
            except ValidationError:
                raise ValueError("Email address is not valid.") from None
            if len(email) > 254:
                raise ValueError("Email address is too long.")

    estate = estate or _get_or_create_estate(company, estate_name)
    plot = _get_or_create_plot(company, estate, plot_number)
    current = _active_sale_on(plot)
    if current is not None:
        raise PlotOccupied(current)
    if buyer is None:
        buyer = Buyer.objects.create(company=company, full_name=buyer_name, phone=phone, email=email)
    try:
        with transaction.atomic():
            return create_sale(company, plot, buyer, price, sale_date, amount_paid, paid_on, actor=actor)
    except IntegrityError:
        current = _active_sale_on(plot)                       # lost a race for the same plot
        if current is not None:
            raise PlotOccupied(current) from None
        raise


@transaction.atomic
def edit_buyer(sale, actor, *, full_name=None, phone=None, email=None):
    """Edit the contact details of the sale's buyer. Only the fields passed are changed. The buyer is a shared
    record: every sale of this buyer shows the change in its history. Nothing is merged and no sale changes
    owner. Raises ValueError (bad input or nothing to change), PermissionDenied (other company)."""
    sale = Sale.objects.get(pk=sale.pk)
    if actor.company_id != sale.company_id:
        raise PermissionDenied("Sale belongs to another company.")
    buyer = Buyer.objects.select_for_update().get(pk=sale.buyer_id)
    new = {}
    if full_name is not None:
        new["full_name"] = _required_text(full_name, "Name", 200)
    if phone is not None:
        value = _clean_text(phone)
        if len(value) > 40:
            raise ValueError("Phone is too long (maximum 40 characters).")
        new["phone"] = value
    if email is not None:
        value = email.strip()
        if value:
            try:
                validate_email(value)
            except ValidationError:
                raise ValueError("Email address is not valid.") from None
        new["email"] = value
    diff = {}
    for name, value in new.items():
        old = getattr(buyer, name)
        if old != value:
            diff[name] = [old, value]
            setattr(buyer, name, value)
    if not diff:
        raise ValueError("Nothing to change.")
    buyer.save(update_fields=list(diff))
    SaleEvent.objects.bulk_create([
        SaleEvent(company_id=sale.company_id, sale_id=pk, actor=actor, action=SaleEvent.Action.BUYER_EDITED,
                  changes=diff, note="Buyer details edited")
        for pk in Sale.objects.filter(buyer=buyer).values_list("pk", flat=True)
    ])
    return buyer


@transaction.atomic
def update_obligation(obligation, actor, note="", **changes):
    """The ONLY way obligations should be edited. Logs before/after and refreshes last_updated_at."""
    obligation = Obligation.objects.select_for_update().get(pk=obligation.pk)
    if actor.company_id != obligation.company_id:
        raise PermissionDenied("Obligation belongs to another company.")
    if obligation.sale.status != Sale.Status.ACTIVE:
        raise SaleStateError("This sale is cancelled, so its obligations are read-only.")
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

def ledger_total(sale):
    """The sum of every ledger entry of this sale. For sales made through the services it equals amount_paid."""
    return Payment.objects.filter(sale=sale).aggregate(total=Sum("amount"))["total"] or Decimal("0")


def open_obligations(company):
    """ACTIVE WORK: an open obligation on an ACTIVE sale. Obligations of cancelled sales are kept for
    audit but never counted here, so every queue, overdue, stale, ageing and exposure figure ignores them."""
    return Obligation.objects.filter(
        company=company, status__in=Obligation.OPEN_STATUSES, sale__status=Sale.Status.ACTIVE)


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


# ---------- cancelling and reinstating a sale ----------

@transaction.atomic
def cancel_sale(sale, actor, reason):
    """Cancel a sale. Its obligations are NOT changed (they keep the state they had) but leave all
    active-work counts and become read-only. Who/when/why is recorded on the sale, in the sale's history
    and in each obligation's history."""
    reason = (reason or "").strip()
    if len(reason) < 3:
        raise ValueError("A reason for the cancellation is required.")
    sale = Sale.objects.select_for_update().get(pk=sale.pk)
    if actor.company_id != sale.company_id:
        raise PermissionDenied("Sale belongs to another company.")
    if sale.status != Sale.Status.ACTIVE:
        raise SaleStateError("This sale is already cancelled.")
    sale.status = Sale.Status.CANCELLED
    sale.cancelled_at = timezone.now()
    sale.cancelled_by = actor
    sale.cancel_reason = reason
    sale.save(update_fields=["status", "cancelled_at", "cancelled_by", "cancel_reason"])
    _log_sale(sale, actor, SaleEvent.Action.CANCELLED, {"status": ["active", "cancelled"]}, reason)
    ObligationEvent.objects.bulk_create([
        ObligationEvent(company=sale.company, obligation=ob, actor=actor, note=f"Sale cancelled: {reason}")
        for ob in sale.obligations.all()
    ])
    return sale


@transaction.atomic
def reinstate_sale(sale, actor, note=""):
    """Undo a mistaken cancellation. Only possible while the plot has not been sold again.
    Any obligation the sale is entitled to but does not have is created now (see `sale.obligations_created`)."""
    sale = Sale.objects.select_for_update().get(pk=sale.pk)
    if actor.company_id != sale.company_id:
        raise PermissionDenied("Sale belongs to another company.")
    if sale.status != Sale.Status.CANCELLED:
        raise SaleStateError("Only a cancelled sale can be reinstated.")
    if Sale.objects.filter(plot=sale.plot, status=Sale.Status.ACTIVE).exists():
        raise SaleStateError("The plot has been sold again, so this cancelled sale cannot be reinstated.")
    sale.status = Sale.Status.ACTIVE
    sale.cancelled_at, sale.cancelled_by, sale.cancel_reason = None, None, ""
    sale.save(update_fields=["status", "cancelled_at", "cancelled_by", "cancel_reason"])
    text = f"Sale reinstated: {note.strip()}" if (note or "").strip() else "Sale reinstated"
    _log_sale(sale, actor, SaleEvent.Action.REINSTATED, {"status": ["cancelled", "active"]}, (note or "").strip())
    ObligationEvent.objects.bulk_create([
        ObligationEvent(company=sale.company, obligation=ob, actor=actor, note=text)
        for ob in sale.obligations.all()
    ])
    sale.obligations_created = generate_obligations(sale)
    return sale


# ---------- bulk update ----------

BULK_LIMIT = 200


class BulkUpdateError(Exception):
    """Some selected obligations cannot be changed. Nothing was changed. `problems` lists each one."""

    def __init__(self, problems):
        self.problems = problems
        super().__init__(f"{len(problems)} obligation(s) cannot be changed.")


def bulk_update_obligations(ids, actor, note="", **changes):
    """Apply the same change to many obligations, ALL OR NOTHING. Each one still goes through
    update_obligation, so each gets its own history entry (who, when, before/after, note)."""
    ids = list(dict.fromkeys(int(i) for i in ids))
    if not ids:
        raise ValueError("Select at least one obligation.")
    if len(ids) > BULK_LIMIT:
        raise ValueError(f"You can change at most {BULK_LIMIT} obligations at once.")
    if not changes and not (note or "").strip():
        raise ValueError("Nothing to change.")
    with transaction.atomic():
        found = {o.pk: o for o in Obligation.objects.filter(pk__in=ids, company_id=actor.company_id).select_related("sale")}
        problems = []
        for pk in ids:
            ob = found.get(pk)
            if ob is None:
                problems.append({"id": pk, "error": "Not found."})
            elif ob.sale.status != Sale.Status.ACTIVE:
                problems.append({"id": pk, "error": "The sale is cancelled."})
        if problems:
            raise BulkUpdateError(problems)
        return [update_obligation(found[pk], actor, note=note, **changes) for pk in ids]
