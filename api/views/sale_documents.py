from django.db.models import Q
from django.utils import timezone
from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from api.mixins import CompanyScopedMixin
from api.serializers.sale_documents import (
    CorrectIssuanceSerializer, IssueSerializer, NotApplicableSerializer, SaleDocumentCreateSerializer,
    SaleDocumentSerializer,
)
from tracker import sale_document_services as documents
from tracker.models import Obligation, Sale, SaleDocument
from tracker.services import SaleStateError

TRUE, FALSE = ("1", "true", "yes"), ("0", "false", "no")


def _refusal(exc):
    """409 for 'wrong state', 400 for bad input, the same mapping the rest of the API uses. A message about one
    input is keyed by that input, as the serializers' own errors are, so a form can show it beside the field."""
    status = 409 if isinstance(exc, SaleStateError) else 400
    field = getattr(exc, "field", None)
    return Response({field: [str(exc)]} if field else {"detail": str(exc)}, status=status)


class SaleDocumentViewSet(CompanyScopedMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
                          viewsets.GenericViewSet):
    """The documents of sales: MD and Manager only. Every write goes through tracker/sale_document_services.py.
    Company-scoped, so another company's document is a 404. There is no DELETE, PUT or PATCH.

    List filters: `sale`, `state`, `document_type`, `ready_to_issue`, `overdue` (true/false) and
    `include_cancelled`. Without `sale`, documents of cancelled sales are left out unless `include_cancelled` is
    set; asking for one sale shows it whatever its status."""
    queryset = (
        SaleDocument.objects
        .select_related("sale__buyer", "sale__plot__estate", "document_type", "obligation__owner",
                        "issued_by", "not_applicable_by", "added_by")
        .order_by("sale_id", "name_snapshot", "id"))
    serializer_class = SaleDocumentSerializer
    http_method_names = ["get", "post", "head", "options"]
    required_actions = {
        "list": "view_sales", "retrieve": "view_sales",
        "create": "manage_sale_documents", "mark_not_applicable": "manage_sale_documents",
        "issue": "issue_document", "correct_issuance": "issue_document",
    }

    # ---- reads ----

    @staticmethod
    def _flag(params, name):
        value = str(params.get(name, "")).strip().lower()
        if value == "":
            return None
        if value in TRUE:
            return True
        if value in FALSE:
            return False
        raise serializers.ValidationError({name: ["Use true or false."]})

    @staticmethod
    def _number(params, name):
        value = str(params.get(name, "")).strip()
        if value == "":
            return None
        if not value.isdigit() or len(value) > 18:
            raise serializers.ValidationError({name: ["Use a whole number."]})
        return int(value)

    def get_queryset(self):
        qs = super().get_queryset()
        if self.action != "list":
            return qs
        params = self.request.query_params
        sale, document_type = self._number(params, "sale"), self._number(params, "document_type")
        if sale is not None:
            qs = qs.filter(sale_id=sale)
        if document_type is not None:
            qs = qs.filter(document_type_id=document_type)
        state = str(params.get("state", "")).strip()
        if state:
            if state not in SaleDocument.State.values:
                raise serializers.ValidationError({"state": ["Choose one of: " + ", ".join(SaleDocument.State.values) + "."]})
            qs = qs.filter(state=state)
        if sale is None and not self._flag(params, "include_cancelled"):
            qs = qs.filter(sale__status=Sale.Status.ACTIVE)
        active_owed = Q(state=SaleDocument.State.OWED, sale__status=Sale.Status.ACTIVE)
        ready = self._flag(params, "ready_to_issue")
        if ready is not None:
            condition = active_owed & Q(obligation__status=Obligation.Status.COMPLETED)
            qs = qs.filter(condition) if ready else qs.exclude(condition)
        late = self._flag(params, "overdue")
        if late is not None:
            condition = (active_owed & Q(obligation__status__in=Obligation.OPEN_STATUSES)
                         & Q(obligation__due_date__lt=timezone.localdate()))
            qs = qs.filter(condition) if late else qs.exclude(condition)
        return qs

    # ---- helpers ----

    def _input(self, serializer_class):
        serializer = serializer_class(data=self.request.data, context={"request": self.request})
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data

    def _fresh(self, pk):
        document = self.queryset.filter(company_id=self.request.user.company_id).get(pk=pk)
        return SaleDocumentSerializer(document, context=self.get_serializer_context()).data

    # ---- writes ----

    def create(self, request, *args, **kwargs):
        data = self._input(SaleDocumentCreateSerializer)
        try:
            document = documents.add_document(
                data["sale"], request.user, data["document_type"], data["state"],
                issued_on=data.get("issued_on"), reference=data.get("reference", ""))
        except (ValueError, SaleStateError) as exc:
            return _refusal(exc)
        return Response(self._fresh(document.pk), status=201)

    @action(detail=True, methods=["post"])
    def issue(self, request, pk=None):
        document = self.get_object()                             # company scoping: another company's is a 404
        data = self._input(IssueSerializer)
        try:
            documents.issue_document(document, request.user, data["issued_on"], reference=data.get("reference", ""))
        except (ValueError, SaleStateError) as exc:
            return _refusal(exc)
        return Response(self._fresh(document.pk))

    @action(detail=True, methods=["post"], url_path="mark-not-applicable", url_name="mark-not-applicable")
    def mark_not_applicable(self, request, pk=None):
        document = self.get_object()
        data = self._input(NotApplicableSerializer)
        try:
            documents.mark_not_applicable(document, request.user, data["reason"])
        except (ValueError, SaleStateError) as exc:
            return _refusal(exc)
        return Response(self._fresh(document.pk))

    @action(detail=True, methods=["post"], url_path="correct-issuance", url_name="correct-issuance")
    def correct_issuance(self, request, pk=None):
        document = self.get_object()
        data = self._input(CorrectIssuanceSerializer)
        try:
            documents.correct_issuance(document, request.user, data["reason"], issued_on=data.get("issued_on"),
                                       reference=data.get("reference"))
        except (ValueError, SaleStateError) as exc:
            return _refusal(exc)
        return Response(self._fresh(document.pk))
