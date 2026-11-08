from django.utils import timezone
from rest_framework import serializers

from api.permissions import can
from tracker.models import DocumentType, Obligation, Sale, SaleDocument
from tracker.sale_document_services import REASON_LIMIT, REFERENCE_LIMIT

OWED, ISSUED, NOT_APPLICABLE = (
    SaleDocument.State.OWED, SaleDocument.State.ISSUED, SaleDocument.State.NOT_APPLICABLE)

# What a person sees for one document. Derived from the document and its obligation, never stored, so the
# two can never disagree: completing the obligation makes it "ready to issue"; only issuing makes it "issued".
DISPLAY_STATUSES = ("issued", "not_required", "outstanding", "in_progress", "ready_to_issue", "needs_review")


def _name(user):
    return (user.get_full_name() or user.username) if user else None


def _day(value):
    return value.isoformat() if value else None


def display_status(document):
    if document.state == ISSUED:
        return "issued"
    if document.state == NOT_APPLICABLE:
        return "not_required"
    obligation = document.obligation
    if obligation is None or obligation.status == Obligation.Status.NOT_APPLICABLE:
        return "needs_review"                       # an owed document whose obligation is missing or contradicts it
    if obligation.status == Obligation.Status.COMPLETED:
        return "ready_to_issue"
    if obligation.status == Obligation.Status.NOT_STARTED:
        return "outstanding"
    return "in_progress"


class SaleDocumentSerializer(serializers.ModelSerializer):
    """One sale document. Needs a queryset that selects the related sale, buyer, plot, estate, catalogue entry,
    obligation (with its owner) and the people named, so a list costs a fixed number of queries."""
    sale_status = serializers.CharField(source="sale.status", read_only=True)
    buyer_name = serializers.CharField(source="sale.buyer.full_name", read_only=True)
    plot_number = serializers.CharField(source="sale.plot.plot_number", read_only=True)
    estate_name = serializers.CharField(source="sale.plot.estate.name", read_only=True)
    name = serializers.CharField(source="name_snapshot", read_only=True)
    catalogue_name = serializers.CharField(source="document_type.name", read_only=True)
    display_status = serializers.SerializerMethodField()
    is_overdue = serializers.SerializerMethodField()
    obligation_status = serializers.SerializerMethodField()
    due_date = serializers.SerializerMethodField()
    owner_name = serializers.SerializerMethodField()
    issued_by_name = serializers.SerializerMethodField()
    not_applicable_by_name = serializers.SerializerMethodField()
    added_by_name = serializers.SerializerMethodField()
    can = serializers.SerializerMethodField()

    class Meta:
        model = SaleDocument
        fields = [
            "id", "sale", "sale_status", "buyer_name", "plot_number", "estate_name", "document_type", "name",
            "catalogue_name", "state", "display_status", "is_overdue", "issued_on", "reference", "issued_by_name",
            "issued_at", "not_applicable_reason", "not_applicable_by_name", "not_applicable_at", "obligation",
            "obligation_status", "due_date", "owner_name", "added_by_name", "added_at", "can",
        ]
        read_only_fields = fields

    def get_display_status(self, obj):
        return display_status(obj)

    def get_is_overdue(self, obj):
        ob = obj.obligation
        return bool(obj.state == OWED and obj.sale.status == Sale.Status.ACTIVE and ob is not None
                    and ob.status in Obligation.OPEN_STATUSES and ob.due_date and ob.due_date < timezone.localdate())

    def get_obligation_status(self, obj):
        return obj.obligation.status if obj.obligation else None

    def get_due_date(self, obj):
        return _day(obj.obligation.due_date) if obj.obligation else None

    def get_owner_name(self, obj):
        return _name(obj.obligation.owner) if obj.obligation else None

    def get_issued_by_name(self, obj):
        return _name(obj.issued_by)

    def get_not_applicable_by_name(self, obj):
        return _name(obj.not_applicable_by)

    def get_added_by_name(self, obj):
        return _name(obj.added_by)

    def get_can(self, obj):
        """What the screen may offer this person for this document: role AND the document's and sale's state.
        Separate from the sale's own `can`, which is not changed."""
        user = self.context["request"].user
        active = obj.sale.status == Sale.Status.ACTIVE
        owed = active and obj.state == OWED
        obligation_ok = obj.obligation is None or obj.obligation.status != Obligation.Status.NOT_APPLICABLE
        return {
            "issue": can(user, "issue_document") and owed and obligation_ok,
            "mark_not_applicable": (
                can(user, "manage_sale_documents")
                and owed
                and obligation_ok
            ),
            "correct_issuance": can(user, "issue_document") and active and obj.state == ISSUED,
        }


class SaleDocumentCreateSerializer(serializers.Serializer):
    """Add a document to a sale. There is deliberately NO obligation field: the service finds or creates the
    right one, so a client can never attach someone else's. Anything not listed here is ignored."""
    sale = serializers.PrimaryKeyRelatedField(queryset=Sale.objects.none())
    document_type = serializers.PrimaryKeyRelatedField(queryset=DocumentType.objects.none())
    state = serializers.ChoiceField(choices=[OWED, ISSUED])
    issued_on = serializers.DateField(required=False, allow_null=True)
    reference = serializers.CharField(required=False, allow_blank=True, max_length=REFERENCE_LIMIT)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        company_id = self.context["request"].user.company_id
        # Another company's sale or document type simply does not exist as a valid choice.
        self.fields["sale"].queryset = Sale.objects.filter(company_id=company_id)
        self.fields["document_type"].queryset = DocumentType.objects.filter(company_id=company_id)


class IssueSerializer(serializers.Serializer):
    issued_on = serializers.DateField()                       # required: never defaulted silently
    reference = serializers.CharField(required=False, allow_blank=True, max_length=REFERENCE_LIMIT)


class NotApplicableSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=REASON_LIMIT)


class CorrectIssuanceSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=REASON_LIMIT)
    issued_on = serializers.DateField(required=False)
    reference = serializers.CharField(required=False, allow_blank=True, max_length=REFERENCE_LIMIT)
