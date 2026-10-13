from decimal import Decimal

from rest_framework import serializers

from api.permissions import can
from api.serializers.reference import BuyerSerializer
from tracker.models import Buyer, Estate, Obligation, Payment, Sale, SaleEvent

_datetime = serializers.DateTimeField().to_representation


def _name(user):
    return (user.get_full_name() or user.username) if user else None


def _money(value):
    return f"{Decimal(value):.2f}"


def _day(value):
    return value.isoformat() if value else None


class SaleDeskListSerializer(serializers.ModelSerializer):
    """One row of the Sales page. Needs a queryset annotated with obligations_total / obligations_open."""
    buyer_name = serializers.CharField(source="buyer.full_name", read_only=True)
    buyer_phone = serializers.CharField(source="buyer.phone", read_only=True)
    plot_number = serializers.CharField(source="plot.plot_number", read_only=True)
    estate = serializers.IntegerField(source="plot.estate_id", read_only=True)
    estate_name = serializers.CharField(source="plot.estate.name", read_only=True)
    is_fully_paid = serializers.BooleanField(read_only=True)
    balance_due = serializers.SerializerMethodField()
    obligations_total = serializers.IntegerField(read_only=True)
    obligations_open = serializers.IntegerField(read_only=True)

    class Meta:
        model = Sale
        fields = [
            "id", "buyer", "buyer_name", "buyer_phone", "plot", "plot_number", "estate", "estate_name",
            "price", "amount_paid", "balance_due", "is_fully_paid", "sale_date", "fully_paid_on", "status",
            "cancelled_at", "cancel_reason", "obligations_total", "obligations_open",
        ]
        read_only_fields = fields

    def get_balance_due(self, obj):
        return _money(max(obj.price - obj.amount_paid, Decimal("0")))


class PaymentSerializer(serializers.ModelSerializer):
    recorded_by_name = serializers.SerializerMethodField()

    class Meta:
        model = Payment
        fields = ["id", "kind", "amount", "paid_on", "reference", "note", "recorded_by_name", "created_at", "reverses"]
        read_only_fields = fields

    def get_recorded_by_name(self, obj):
        return _name(obj.recorded_by)


class SaleDeskDetailSerializer(SaleDeskListSerializer):
    buyer_detail = BuyerSerializer(source="buyer", read_only=True)
    obligations = serializers.SerializerMethodField()
    payments = serializers.SerializerMethodField()
    history = serializers.SerializerMethodField()
    can = serializers.SerializerMethodField()

    class Meta(SaleDeskListSerializer.Meta):
        fields = SaleDeskListSerializer.Meta.fields + ["buyer_detail", "obligations", "payments", "history", "can"]
        read_only_fields = fields

    def get_obligations(self, obj):
        active = obj.status == Sale.Status.ACTIVE
        rows = (Obligation.objects.filter(sale=obj).select_related("obligation_type", "owner")
                .order_by("obligation_type__name", "id"))
        return [{
            "id": o.pk, "type_name": o.obligation_type.name, "status": o.status, "owner_name": _name(o.owner),
            "started_on": _day(o.started_on), "due_date": _day(o.due_date), "completed_on": _day(o.completed_on),
            "is_open": active and o.status in Obligation.OPEN_STATUSES,
        } for o in rows]

    def get_payments(self, obj):
        active = obj.status == Sale.Status.ACTIVE
        payments = list(Payment.objects.filter(sale=obj).select_related("recorded_by").order_by("created_at", "id"))
        reversed_ids = {p.reverses_id for p in payments if p.reverses_id}
        return [{
            "id": p.pk, "kind": p.kind, "amount": _money(p.amount), "paid_on": _day(p.paid_on),
            "reference": p.reference, "note": p.note, "recorded_by_name": _name(p.recorded_by),
            "created_at": _datetime(p.created_at), "reverses": p.reverses_id,
            "is_reversed": p.pk in reversed_ids,
            "reversible": active and p.kind != Payment.Kind.REVERSAL and p.amount > 0 and p.pk not in reversed_ids,
        } for p in payments]

    def get_history(self, obj):
        events = SaleEvent.objects.filter(sale=obj).select_related("actor").order_by("created_at", "id")
        return [{
            "id": e.pk, "action": e.action, "action_label": e.get_action_display(), "actor_name": _name(e.actor),
            "created_at": _datetime(e.created_at), "note": e.note,
            "changes_display": [{"field": field, "from": pair[0], "to": pair[1]} for field, pair in e.changes.items()],
        } for e in events]

    def get_can(self, obj):
        """What the screen may offer this person for this sale: role AND the sale's state."""
        user = self.context["request"].user
        active = obj.status == Sale.Status.ACTIVE
        resold = (not active) and Sale.objects.filter(plot_id=obj.plot_id, status=Sale.Status.ACTIVE).exists()
        return {
            "record_payment": can(user, "record_payment") and active and obj.amount_paid < obj.price,
            "reverse_payment": can(user, "reverse_payment") and active,
            "edit_buyer": can(user, "edit_buyer"),
            "cancel": can(user, "manage_sales") and active,
            "reinstate": can(user, "manage_sales") and (not active) and not resold,
        }


class SaleCreateSerializer(serializers.Serializer):
    """Add a sale. Amounts are taken as text and checked by tracker/services.py, the one place with the rules."""
    estate = serializers.PrimaryKeyRelatedField(queryset=Estate.objects.none(), required=False)
    estate_name = serializers.CharField(required=False, allow_blank=True, max_length=200)
    plot_number = serializers.CharField(max_length=50)
    buyer = serializers.PrimaryKeyRelatedField(queryset=Buyer.objects.none(), required=False)
    buyer_name = serializers.CharField(required=False, allow_blank=True, max_length=200)
    phone = serializers.CharField(required=False, allow_blank=True, max_length=40)
    email = serializers.CharField(required=False, allow_blank=True, max_length=254)
    price = serializers.CharField()
    amount_paid = serializers.CharField(required=False, default="0")
    sale_date = serializers.DateField()
    paid_on = serializers.DateField(required=False, allow_null=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        company_id = self.context["request"].user.company_id
        # Another company's estate or buyer simply does not exist as a valid choice.
        self.fields["estate"].queryset = Estate.objects.filter(company_id=company_id)
        self.fields["buyer"].queryset = Buyer.objects.filter(company_id=company_id)


class PaymentCreateSerializer(serializers.Serializer):
    amount = serializers.CharField()
    paid_on = serializers.DateField(required=False, allow_null=True)
    reference = serializers.CharField(required=False, allow_blank=True, max_length=120)
    note = serializers.CharField(required=False, allow_blank=True)
    idempotency_key = serializers.CharField(max_length=64)       # required: one key per submit of the form


class ReversePaymentSerializer(serializers.Serializer):
    reason = serializers.CharField()


class BuyerEditSerializer(serializers.Serializer):
    full_name = serializers.CharField(required=False, allow_blank=True, max_length=200)
    phone = serializers.CharField(required=False, allow_blank=True, max_length=40)
    email = serializers.CharField(required=False, allow_blank=True, max_length=254)


class CancelSerializer(serializers.Serializer):
    reason = serializers.CharField()


class ReinstateSerializer(serializers.Serializer):
    note = serializers.CharField(required=False, allow_blank=True)
