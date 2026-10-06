from django.db.models import F
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from api.filters import ObligationFilter
from api.mixins import CompanyScopedMixin
from api.permissions import ActionPermission, CanEditObligation
from api.serializers.obligations import (
    ObligationDetailSerializer, ObligationEventSerializer, ObligationListSerializer,
    ObligationTypeSerializer, ObligationUpdateSerializer,
)
from tracker import services
from tracker.models import Department, Obligation, ObligationType, Sale, User


class ObligationTypeViewSet(
    CompanyScopedMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
    mixins.CreateModelMixin, mixins.UpdateModelMixin, viewsets.GenericViewSet,
):
    """Everyone can read the types; only MD/Manager can change them. No DELETE: deactivate instead
    (obligations keep pointing at their type). Editing a type never rewrites existing obligations."""
    queryset = ObligationType.objects.select_related("default_department").order_by("name")
    serializer_class = ObligationTypeSerializer
    # No DestroyModelMixin on purpose: DELETE is answered with 405.
    http_method_names = ["get", "post", "put", "patch", "head", "options"]
    required_actions = {
        "list": "view_reference", "retrieve": "view_reference",
        "create": "manage_settings", "update": "manage_settings", "partial_update": "manage_settings",
    }


class ObligationViewSet(CompanyScopedMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    queryset = Obligation.objects.select_related(
        "sale__buyer", "sale__plot__estate", "obligation_type", "owner", "department",
    ).order_by(F("due_date").asc(nulls_last=True), "id")
    permission_classes = [ActionPermission, CanEditObligation]
    required_actions = {
        "list": "view_obligations", "retrieve": "view_obligations",
        "partial_update": "edit_obligation", "events": "view_history",
    }
    http_method_names = ["get", "patch", "head", "options"]
    filterset_class = ObligationFilter
    ordering_fields = ["due_date", "started_on", "last_updated_at", "status"]

    def get_queryset(self):
        qs = super().get_queryset()
        include = str(self.request.query_params.get("include_cancelled", "")).lower() in ("1", "true", "yes")
        if self.action == "list" and not include:
            qs = qs.filter(sale__status=Sale.Status.ACTIVE)       # cancelled sales: audit only, off the work lists
        return qs

    def get_serializer_class(self):
        return ObligationListSerializer if self.action == "list" else ObligationDetailSerializer

    def partial_update(self, request, *args, **kwargs):
        obligation = self.get_object()                       # company scoping + object-level permission
        serializer = ObligationUpdateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        note = data.pop("note", "")
        try:
            services.update_obligation(obligation, request.user, note=note, **data)   # the ONLY write path
        except services.SaleStateError as exc:
            return Response({"detail": str(exc)}, status=409)
        fresh = self.get_queryset().get(pk=obligation.pk)
        return Response(ObligationDetailSerializer(fresh, context=self.get_serializer_context()).data)

    @action(detail=True, methods=["get"])
    def events(self, request, pk=None):
        obligation = self.get_object()
        events = list(obligation.events.select_related("actor").order_by("created_at", "id"))
        user_ids, dept_ids = set(), set()
        for ev in events:
            for field, pair in ev.changes.items():
                ids = {x for x in pair if x is not None}
                if field == "owner":
                    user_ids |= ids
                elif field == "department":
                    dept_ids |= ids
        company_id = request.user.company_id
        names = {
            "owner": {u.pk: (u.get_full_name() or u.username)
                      for u in User.objects.filter(pk__in=user_ids, company_id=company_id)},
            "department": {d.pk: d.name for d in Department.objects.filter(pk__in=dept_ids, company_id=company_id)},
        }
        context = {**self.get_serializer_context(), "names": names}
        return Response(ObligationEventSerializer(events, many=True, context=context).data)
