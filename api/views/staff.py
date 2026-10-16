from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django_filters import rest_framework as df
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from api.mixins import CompanyScopedMixin
from api.serializers.staff import (
    AccountEventSerializer, DeactivateSerializer, LinkRequestSerializer, StaffCreateSerializer, StaffSerializer,
    StaffUpdateSerializer,
)
from tracker import account_services as acc
from tracker import staff_services
from tracker.models import Obligation, Sale, User

_REFUSED = (
    staff_services.StaffValidationError, PermissionDenied, staff_services.StaffStateError, acc.AccountLinkError,
    ValueError,
)


def _refusal(exc):
    """Same mapping as the rest of the API: 400 bad input, 403 not allowed, 409 wrong state."""
    if isinstance(exc, staff_services.StaffValidationError):
        return Response(exc.errors, status=400)
    if isinstance(exc, PermissionDenied):
        return Response({"detail": str(exc) or "You are not allowed to do this."}, status=403)
    if isinstance(exc, (staff_services.StaffStateError, acc.AccountLinkError)):
        return Response({"detail": str(exc)}, status=409)
    return Response({"detail": str(exc)}, status=400)


def _no_store(response):
    """A response that may carry a one-time link must never be cached by the browser or a proxy."""
    response["Cache-Control"] = "no-store"
    response["Pragma"] = "no-cache"
    return response


class StaffFilter(df.FilterSet):
    q = df.CharFilter(method="filter_q")
    role = df.ChoiceFilter(field_name="role", choices=User.Role.choices)
    is_active = df.BooleanFilter(field_name="is_active")
    department = df.NumberFilter(field_name="department_id")

    class Meta:
        model = User
        fields = []

    def filter_q(self, qs, name, value):
        value = (value or "").strip()
        if not value:
            return qs
        return qs.filter(
            Q(first_name__icontains=value) | Q(last_name__icontains=value)
            | Q(email__icontains=value) | Q(username__icontains=value))


class StaffViewSet(CompanyScopedMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Staff account management for MD and Manager (action `manage_users`). Company-scoped, so another
    company's account is a 404. Every rule that matters (who may manage whom, protected admin accounts, own
    role, last MD) is enforced in tracker/staff_services.py; hiding a button is never the protection."""
    queryset = (
        User.objects.select_related("department")
        .annotate(open_obligations=Count(
            "owned_obligations", distinct=True,
            filter=Q(owned_obligations__status__in=Obligation.OPEN_STATUSES,
                     owned_obligations__sale__status=Sale.Status.ACTIVE)))
        .order_by("last_name", "first_name", "username"))
    serializer_class = StaffSerializer
    filterset_class = StaffFilter
    ordering_fields = ["last_name", "first_name", "username", "role", "is_active", "last_login"]
    http_method_names = ["get", "post", "patch", "head", "options"]
    required_actions = {
        "list": "manage_users", "retrieve": "manage_users", "create": "manage_users",
        "partial_update": "manage_users", "deactivate": "manage_users", "reactivate": "manage_users",
        "open_obligations": "manage_users", "setup_link": "manage_users", "reset_link": "manage_users",
        "history": "manage_users", "role_options": "manage_users",
    }

    def _detail(self, pk):
        return StaffSerializer(self.get_queryset().get(pk=pk), context=self.get_serializer_context()).data

    def _input(self, serializer_class):
        serializer = serializer_class(data=self.request.data, context={"request": self.request})
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data

    # ---- reads ----

    @action(detail=False, methods=["get"], url_path="options", url_name="options")
    def role_options(self, request):
        """The roles the signed-in administrator may create or assign."""
        return Response({"roles": staff_services.role_choices(request.user)})

    @action(detail=True, methods=["get"], url_path="open-obligations", url_name="open-obligations")
    def open_obligations(self, request, pk=None):
        """For the deactivation screen. Deactivating never reassigns these: they stay with this person."""
        target = self.get_object()
        return Response({"owner": target.pk, "count": staff_services.open_obligation_count(target)})

    @action(detail=True, methods=["get"])
    def history(self, request, pk=None):
        target = self.get_object()
        try:
            events = staff_services.account_history(request.user, target)
        except _REFUSED as exc:
            return _refusal(exc)
        page = self.paginate_queryset(events)
        return self.get_paginated_response(AccountEventSerializer(page, many=True).data)

    # ---- writes ----

    def create(self, request, *args, **kwargs):
        data = self._input(StaffCreateSerializer)
        try:
            user = staff_services.create_staff(
                request.user, email=data["email"], first_name=data["first_name"], last_name=data["last_name"],
                role=data["role"], department=data.get("department"))
        except _REFUSED as exc:
            return _refusal(exc)
        return Response(self._detail(user.pk), status=201)

    def partial_update(self, request, *args, **kwargs):
        target = self.get_object()
        data = self._input(StaffUpdateSerializer)
        try:
            staff_services.update_staff(
                request.user, target.pk, **{k: data[k] for k in ("first_name", "last_name", "department", "role") if k in data})
        except _REFUSED as exc:
            return _refusal(exc)
        return Response(self._detail(target.pk))

    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        target = self.get_object()
        data = self._input(DeactivateSerializer)
        try:
            result = staff_services.deactivate(request.user, target.pk, data.get("reason", ""))
        except _REFUSED as exc:
            return _refusal(exc)
        body = dict(self._detail(target.pk))
        body["open_obligations_remaining"] = result.open_obligations
        return Response(body)

    @action(detail=True, methods=["post"])
    def reactivate(self, request, pk=None):
        target = self.get_object()
        try:
            staff_services.reactivate(request.user, target.pk)
        except _REFUSED as exc:
            return _refusal(exc)
        return Response(self._detail(target.pk))

    def _issue(self, request, purpose):
        target = self.get_object()
        data = self._input(LinkRequestSerializer)
        try:
            outcome = staff_services.issue_link_for(
                request.user, target.pk, purpose, reveal_if_email_fails=data["reveal_if_email_fails"])
        except _REFUSED as exc:
            return _no_store(_refusal(exc))
        body = {"purpose": purpose, "status": outcome.status, "expires_at": outcome.expires_at.isoformat()}
        if outcome.status == "revealed":
            body["link"] = outcome.link            # shown once, never stored, never returned by any other call
        elif outcome.status == "email_failed":
            body["detail"] = ("The email could not be sent. Ask again with reveal_if_email_fails to have the new "
                              "link shown to you once instead.")
        return _no_store(Response(body))

    @action(detail=True, methods=["post"], url_path="setup-link", url_name="setup-link")
    def setup_link(self, request, pk=None):
        return self._issue(request, acc.SETUP)

    @action(detail=True, methods=["post"], url_path="reset-link", url_name="reset-link")
    def reset_link(self, request, pk=None):
        return self._issue(request, acc.RESET)
