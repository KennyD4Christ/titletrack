from django.db.models import Prefetch
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from api.mixins import CompanyScopedMixin
from api.serializers.documents import (
    DocumentTypeCreateSerializer, DocumentTypeRenameSerializer, DocumentTypeSerializer,
    DocumentTypeUpdateSerializer,
)
from tracker import document_services as catalogue
from tracker.models import DocumentType, ObligationType


def _refusal(exc):
    """409 for 'wrong state', 400 for bad input; a message about one input is keyed by that input, as the
    serializers' own errors are, so a form can show it beside the field."""
    status = 409 if isinstance(exc, catalogue.CatalogueStateError) else 400
    field = getattr(exc, "field", None)
    return Response({field: [str(exc)]} if field else {"detail": str(exc)}, status=status)


class DocumentTypeViewSet(CompanyScopedMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
                          viewsets.GenericViewSet):
    """The company's document catalogue. Everyone can read it; only MD and Manager can change it. No DELETE:
    a used entry is deactivated instead. Every write goes through tracker/document_services.py. Company-scoped,
    so another company's entry is a 404. List: `?is_active=true|false`."""
    queryset = (
        DocumentType.objects
        .prefetch_related(Prefetch("obligation_types", queryset=ObligationType.objects.filter(is_current=True),
                                   to_attr="current_types"))
        .order_by("sort_order", "name", "id"))
    serializer_class = DocumentTypeSerializer
    http_method_names = ["get", "post", "patch", "head", "options"]
    required_actions = {
        "list": "view_reference", "retrieve": "view_reference",
        "create": "manage_document_catalogue", "partial_update": "manage_document_catalogue",
        "rename": "manage_document_catalogue", "activate": "manage_document_catalogue",
        "deactivate": "manage_document_catalogue",
    }

    def get_queryset(self):
        qs = super().get_queryset()
        flag = str(self.request.query_params.get("is_active", "")).lower()
        if self.action == "list" and flag in ("1", "true", "yes"):
            qs = qs.filter(is_active=True)
        elif self.action == "list" and flag in ("0", "false", "no"):
            qs = qs.filter(is_active=False)
        return qs

    def _input(self, serializer_class):
        serializer = serializer_class(data=self.request.data, context={"request": self.request})
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data

    def _fresh(self, pk):
        entry = self.queryset.filter(company_id=self.request.user.company_id).get(pk=pk)
        return DocumentTypeSerializer(entry, context=self.get_serializer_context()).data

    def create(self, request, *args, **kwargs):
        data = self._input(DocumentTypeCreateSerializer)
        try:
            entry = catalogue.create_document_type(
                request.user.company, request.user, name=data["name"], description=data.get("description", ""),
                category=data.get("category", ""), sort_order=data.get("sort_order", 0),
                sla_days=data.get("sla_days"), default_department=data.get("default_department"))
        except catalogue.CatalogueError as exc:
            return _refusal(exc)
        return Response(self._fresh(entry.pk), status=201)

    def partial_update(self, request, *args, **kwargs):
        entry = self.get_object()                                  # company scoping: another company's entry is a 404
        data = self._input(DocumentTypeUpdateSerializer)
        try:
            catalogue.update_document_type(
                entry, request.user, description=data.get("description"), category=data.get("category"),
                sort_order=data.get("sort_order"), sla_days=data.get("sla_days"),
                default_department=data["default_department"] if "default_department" in data else catalogue.UNSET)
        except catalogue.CatalogueError as exc:
            return _refusal(exc)
        return Response(self._fresh(entry.pk))

    @action(detail=True, methods=["post"])
    def rename(self, request, pk=None):
        entry = self.get_object()
        data = self._input(DocumentTypeRenameSerializer)
        try:
            catalogue.rename_document_type(entry, request.user, data["name"])
        except catalogue.CatalogueError as exc:
            return _refusal(exc)
        return Response(self._fresh(entry.pk))

    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        entry = self.get_object()
        try:
            catalogue.set_document_type_active(entry, request.user, True)
        except catalogue.CatalogueError as exc:
            return _refusal(exc)
        return Response(self._fresh(entry.pk))

    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        entry = self.get_object()
        try:
            catalogue.set_document_type_active(entry, request.user, False)
        except catalogue.CatalogueError as exc:
            return _refusal(exc)
        return Response(self._fresh(entry.pk))
