from decimal import Decimal

from rest_framework import serializers
from drf_spectacular.utils import extend_schema_field, inline_serializer

from api.permissions import can
from api.serializers.reference import BuyerSerializer
from tracker.models import Buyer, DocumentType, Estate, Obligation, Payment, Sale, SaleDocument, SaleEvent
from tracker.sale_document_services import REFERENCE_LIMIT, SaleDocumentError, _issue_date, _reference
from tracker.services import _clean_text

_datetime = serializers.DateTimeField().to_representation


def _name(user):
    return (user.get_full_name() or user.username) if user else None


def _money(value):
    return f"{Decimal(value):.2f}"


def _day(value):
    return value.isoformat() if value else None


class SaleDeskObligationSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    type_name = serializers.CharField()
    status = serializers.ChoiceField(choices=Obligation.Status.choices)
    owner_name = serializers.CharField(allow_null=True)
    started_on = serializers.DateField()
    due_date = serializers.DateField(allow_null=True)
    completed_on = serializers.DateField(allow_null=True)
    is_open = serializers.BooleanField()


class SaleDeskPaymentSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    kind = serializers.ChoiceField(choices=Payment.Kind.choices)
    amount = serializers.CharField()
    paid_on = serializers.DateField()
    reference = serializers.CharField()
    note = serializers.CharField()
    recorded_by_name = serializers.CharField(allow_null=True)
    created_at = serializers.DateTimeField()
    reverses = serializers.IntegerField(allow_null=True)
    is_reversed = serializers.BooleanField()
    reversible = serializers.BooleanField()


class SaleDeskHistorySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    action = serializers.ChoiceField(choices=SaleEvent.Action.choices)
    action_label = serializers.CharField()
    actor_name = serializers.CharField(allow_null=True)
    created_at = serializers.DateTimeField()
    note = serializers.CharField()
    changes_display = inline_serializer(
        name="SaleDeskHistoryChange",
        fields={
            "field": serializers.CharField(),
            "from": serializers.JSONField(),
            "to": serializers.JSONField(),
        },
        many=True,
    )


class SaleDeskCanSerializer(serializers.Serializer):
    record_payment = serializers.BooleanField()
    reverse_payment = serializers.BooleanField()
    edit_buyer = serializers.BooleanField()
    cancel = serializers.BooleanField()
    reinstate = serializers.BooleanField()


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

    @extend_schema_field(serializers.CharField())
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

    @extend_schema_field(SaleDeskObligationSerializer(many=True))
    def get_obligations(self, obj):
        active = obj.status == Sale.Status.ACTIVE
        rows = (Obligation.objects.filter(sale=obj).select_related("obligation_type", "owner")
                .order_by("obligation_type__name", "id"))
        return [{
            "id": o.pk, "type_name": o.obligation_type.name, "status": o.status, "owner_name": _name(o.owner),
            "started_on": _day(o.started_on), "due_date": _day(o.due_date), "completed_on": _day(o.completed_on),
            "is_open": active and o.status in Obligation.OPEN_STATUSES,
        } for o in rows]

    @extend_schema_field(SaleDeskPaymentSerializer(many=True))
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

    @extend_schema_field(SaleDeskHistorySerializer(many=True))
    def get_history(self, obj):
        events = SaleEvent.objects.filter(sale=obj).select_related("actor").order_by("created_at", "id")
        return [{
            "id": e.pk, "action": e.action, "action_label": e.get_action_display(), "actor_name": _name(e.actor),
            "created_at": _datetime(e.created_at), "note": e.note,
            "changes_display": [{"field": field, "from": pair[0], "to": pair[1]} for field, pair in e.changes.items()],
        } for e in events]

    @extend_schema_field(SaleDeskCanSerializer())
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


MAX_DOCUMENTS = 50
_OWED, _ISSUED = SaleDocument.State.OWED, SaleDocument.State.ISSUED


class SaleCreateDocumentList(serializers.ListSerializer):
    """The `documents` list of a new sale. It remembers which document types the request has chosen so far, so a
    type chosen twice is reported against its LATER position, in the same answer as every other problem."""

    def to_internal_value(self, data):
        self.chosen_types = set()
        return super().to_internal_value(data)


class SaleCreateDocumentSerializer(serializers.Serializer):
    """One document chosen while a sale is created: owed, or already issued (with its issue date).
    There is deliberately NO obligation or company field: tracker/sale_document_services.py finds or creates the
    obligation and uses the sale's own company, so a client can never attach someone else's. Anything not listed
    here is ignored. The types offered are the caller's own company's (set in SaleCreateSerializer.__init__)."""
    document_type = serializers.PrimaryKeyRelatedField(queryset=DocumentType.objects.none())
    state = serializers.ChoiceField(choices=[_OWED, _ISSUED])
    issued_on = serializers.DateField(required=False, allow_null=True)
    reference = serializers.CharField(required=False, allow_blank=True, max_length=REFERENCE_LIMIT)

    class Meta:
        list_serializer_class = SaleCreateDocumentList

    def validate_document_type(self, entry):
        chosen = getattr(self.parent, "chosen_types", None)
        if chosen is not None:
            if entry.pk in chosen:
                raise serializers.ValidationError("This document type is already in the list.")
            chosen.add(entry.pk)
        return entry

    def validate(self, attrs):
        """The same rules (and wording) as add_document, checked here so every problem in the list is reported at
        once. add_document checks them again, so this is only a convenience, never the only guard."""
        if attrs["state"] == _ISSUED:
            try:
                _issue_date(attrs.get("issued_on"))
                _reference(attrs.get("reference", ""))
            except SaleDocumentError as exc:
                raise serializers.ValidationError({exc.field: [str(exc)]}) from None
        elif attrs.get("issued_on") is not None or _clean_text(attrs.get("reference", "")):
            raise serializers.ValidationError(
                {"issued_on": ["An issue date and reference can only be given for an issued document."]})
        return attrs


class SaleCreateSerializer(serializers.Serializer):
    """Add a sale. Amounts are taken as text and checked by tracker/services.py, the one place with the rules.

    `documents` is optional. Omitted or [] creates the sale exactly as before; a non-empty list creates it
    document-managed with exactly those documents (null is refused)."""
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
    documents = SaleCreateDocumentSerializer(many=True, required=False, max_length=MAX_DOCUMENTS)
    paid_on = serializers.DateField(required=False, allow_null=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        if request is not None and request.user.is_authenticated:
            company_id = request.user.company_id
            # Another company's estate or buyer simply does not exist as a valid choice.
            self.fields["estate"].queryset = Estate.objects.filter(company_id=company_id)
            self.fields["buyer"].queryset = Buyer.objects.filter(company_id=company_id)
            # Likewise another company's document type does not exist as a choice. An INACTIVE type of the caller's
            # own company is a valid choice here and is refused (409) by add_document.
            self.fields["documents"].child.fields["document_type"].queryset = DocumentType.objects.filter(
                company_id=company_id)


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
