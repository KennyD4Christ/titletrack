from django.utils import timezone
from rest_framework import serializers

from api.serializers.reference import BuyerSerializer, SaleSerializer
from tracker import computed
from tracker.models import Department, Obligation, ObligationEvent, ObligationType, User


def _person_name(user):
    return (user.get_full_name() or user.username) if user else None


class ClockMixin:
    """Compute 'today' and 'now' once per request so every row in a list uses the same clock."""

    def _clock(self):
        ctx = self.context
        if "_today" not in ctx:
            ctx["_now"] = timezone.now()
            ctx["_today"] = timezone.localdate()
        return ctx["_today"], ctx["_now"]


class ObligationListSerializer(ClockMixin, serializers.ModelSerializer):
    buyer_name = serializers.CharField(source="sale.buyer.full_name", read_only=True)
    plot_number = serializers.CharField(source="sale.plot.plot_number", read_only=True)
    estate = serializers.IntegerField(source="sale.plot.estate_id", read_only=True)
    estate_name = serializers.CharField(source="sale.plot.estate.name", read_only=True)
    type_name = serializers.CharField(source="obligation_type.name", read_only=True)
    owner_name = serializers.SerializerMethodField()
    department_name = serializers.CharField(source="department.name", read_only=True, allow_null=True)
    is_open = serializers.SerializerMethodField()
    days_outstanding = serializers.SerializerMethodField()
    days_overdue = serializers.SerializerMethodField()
    staleness = serializers.SerializerMethodField()
    is_incomplete = serializers.SerializerMethodField()

    class Meta:
        model = Obligation
        fields = [
            "id", "sale", "buyer_name", "plot_number", "estate", "estate_name",
            "obligation_type", "type_name", "status", "stage",
            "owner", "owner_name", "department", "department_name", "external_party",
            "next_action", "next_review_date", "started_on", "due_date", "last_updated_at",
            "is_open", "days_outstanding", "days_overdue", "staleness", "is_incomplete",
        ]
        read_only_fields = fields

    def get_owner_name(self, obj):
        return _person_name(obj.owner)

    def get_is_open(self, obj):
        return computed.is_open(obj)

    def get_days_outstanding(self, obj):
        return computed.days_outstanding(obj, self._clock()[0])

    def get_days_overdue(self, obj):
        return computed.days_overdue(obj, self._clock()[0])

    def get_staleness(self, obj):
        return computed.staleness(obj, self._clock()[1])

    def get_is_incomplete(self, obj):
        return computed.is_incomplete(obj)


class ObligationDetailSerializer(ObligationListSerializer):
    reason = serializers.CharField(read_only=True)
    completed_on = serializers.DateField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    buyer = BuyerSerializer(source="sale.buyer", read_only=True)
    sale_summary = SaleSerializer(source="sale", read_only=True)
    timeline = serializers.SerializerMethodField()

    class Meta(ObligationListSerializer.Meta):
        fields = ObligationListSerializer.Meta.fields + [
            "reason", "completed_on", "created_at", "buyer", "sale_summary", "timeline",
        ]
        read_only_fields = fields

    def get_timeline(self, obj):
        """Data for the 'Why is this overdue?' panel."""
        today = self._clock()[0]
        return {
            "sla_days": obj.obligation_type.sla_days,
            "expected_days": (obj.due_date - obj.started_on).days if obj.due_date else None,
            "actual_days": (today - obj.started_on).days if computed.is_open(obj)
            else ((obj.completed_on - obj.started_on).days if obj.completed_on else None),
            "delay_days": computed.days_overdue(obj, today),
        }


class ObligationUpdateSerializer(serializers.Serializer):
    """The ONLY fields a client may change. Anything else in the request is ignored."""
    status = serializers.ChoiceField(choices=Obligation.Status.choices, required=False)
    stage = serializers.CharField(max_length=120, required=False, allow_blank=True)
    owner = serializers.PrimaryKeyRelatedField(queryset=User.objects.none(), required=False, allow_null=True)
    department = serializers.PrimaryKeyRelatedField(queryset=Department.objects.none(), required=False, allow_null=True)
    external_party = serializers.CharField(max_length=200, required=False, allow_blank=True)
    reason = serializers.CharField(required=False, allow_blank=True)
    next_action = serializers.CharField(required=False, allow_blank=True)
    next_review_date = serializers.DateField(required=False, allow_null=True)
    note = serializers.CharField(required=False, allow_blank=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        company_id = self.context["request"].user.company_id
        # People and departments from another company simply do not exist as valid choices.
        self.fields["owner"].queryset = User.objects.filter(company_id=company_id, is_active=True)
        self.fields["department"].queryset = Department.objects.filter(company_id=company_id)

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError("Nothing to update.")
        return attrs


class ObligationEventSerializer(serializers.ModelSerializer):
    actor_name = serializers.SerializerMethodField()
    changes_display = serializers.SerializerMethodField()

    class Meta:
        model = ObligationEvent
        fields = ["id", "created_at", "actor", "actor_name", "note", "changes", "changes_display"]

    def get_actor_name(self, obj):
        return _person_name(obj.actor)

    def get_changes_display(self, obj):
        names = self.context.get("names", {})
        rows = []
        for field, (old, new) in obj.changes.items():
            lookup = names.get(field)
            rows.append({
                "field": field,
                "from": lookup.get(old, old) if lookup and old is not None else old,
                "to": lookup.get(new, new) if lookup and new is not None else new,
            })
        return rows


class ObligationTypeSerializer(serializers.ModelSerializer):
    default_department = serializers.PrimaryKeyRelatedField(
        queryset=Department.objects.none(), required=False, allow_null=True)

    class Meta:
        model = ObligationType
        fields = ["id", "name", "trigger", "sla_days", "default_department", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        if request is not None:
            self.fields["default_department"].queryset = Department.objects.filter(company_id=request.user.company_id)

    def validate_sla_days(self, value):
        if not 1 <= value <= 3650:
            raise serializers.ValidationError("SLA days must be between 1 and 3650.")
        return value

    def validate_name(self, value):
        value = value.strip()
        clash = ObligationType.objects.filter(company_id=self.context["request"].user.company_id, name__iexact=value)
        if self.instance is not None:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError("An obligation type with this name already exists.")
        return value
