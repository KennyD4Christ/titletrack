from datetime import timedelta
from decimal import Decimal

from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from api.permissions import ActionPermission
from api.serializers.obligations import ObligationListSerializer
from tracker import reports, services
from tracker.models import User

FULL_DASHBOARD_ROLES = ("md", "manager", "viewer")        # roles allowed the company-wide dashboard (view_dashboard)


def money(value):
    return f"{Decimal(value or 0):.2f}"


def _rows(rows, *money_keys):
    out = []
    for r in rows:
        r = dict(r)
        for k in money_keys:
            r[k] = money(r[k])
        out.append(r)
    return out


def _clock():
    return timezone.localdate(), timezone.now()


def exposure_payload(company, today):
    e = reports.exposure_summary(company, today)
    return {"amount": money(e["amount"]), "buyers": e["buyers"], "sales": e["sales"],
            "fully_paid_amount": money(e["fully_paid_amount"]), "fully_paid_buyers": e["fully_paid_buyers"],
            "average_wait_days": e["average_wait_days"]}


def workload_payload(company, today, now):
    w = reports.workload(company, today, now)
    return {"by_department": w["by_department"], "by_owner": w["by_owner"][:10]}


def oldest_payload(request, company, limit=10):
    return ObligationListSerializer(reports.oldest_cases(company, limit), many=True, context={"request": request}).data



class AttentionSummarySerializer(serializers.Serializer):
    open = serializers.IntegerField()
    overdue = serializers.IntegerField()
    due_soon = serializers.IntegerField()
    stale = serializers.IntegerField()
    awaiting_external = serializers.IntegerField()
    incomplete = serializers.IntegerField()


class ExposureSummarySerializer(serializers.Serializer):
    amount = serializers.CharField()
    buyers = serializers.IntegerField()
    sales = serializers.IntegerField()
    fully_paid_amount = serializers.CharField()
    fully_paid_buyers = serializers.IntegerField()
    average_wait_days = serializers.FloatField(allow_null=True)


class AgeingSerializer(serializers.Serializer):
    d0_30 = serializers.IntegerField()
    d31_60 = serializers.IntegerField()
    d61_90 = serializers.IntegerField()
    d90_plus = serializers.IntegerField()


class EstateDashboardRowSerializer(serializers.Serializer):
    estate_id = serializers.IntegerField()
    estate = serializers.CharField()
    amount = serializers.CharField()
    buyers = serializers.IntegerField()
    sales = serializers.IntegerField()
    open_obligations = serializers.IntegerField()
    overdue = serializers.IntegerField()
    oldest_days = serializers.IntegerField(allow_null=True)


class TypeDashboardRowSerializer(serializers.Serializer):
    type_id = serializers.IntegerField()
    type = serializers.CharField()
    open = serializers.IntegerField()
    overdue = serializers.IntegerField()
    buyers = serializers.IntegerField()
    amount = serializers.CharField()
    oldest_days = serializers.IntegerField(allow_null=True)


class DelayReasonSerializer(serializers.Serializer):
    reason = serializers.CharField()
    count = serializers.IntegerField()


class DepartmentWorkloadSerializer(serializers.Serializer):
    department = serializers.CharField()
    open = serializers.IntegerField()
    overdue = serializers.IntegerField()
    stale = serializers.IntegerField()
    awaiting_external = serializers.IntegerField()
    unowned = serializers.IntegerField()


class OwnerWorkloadSerializer(serializers.Serializer):
    owner_id = serializers.IntegerField()
    owner = serializers.CharField()
    open = serializers.IntegerField()
    overdue = serializers.IntegerField()
    stale = serializers.IntegerField()
    awaiting_external = serializers.IntegerField()
    departments = serializers.ListField(
        child=serializers.CharField()
    )


class WorkloadSerializer(serializers.Serializer):
    by_department = DepartmentWorkloadSerializer(many=True)
    by_owner = OwnerWorkloadSerializer(many=True)


class StaffDashboardQueueSerializer(serializers.Serializer):
    overdue = serializers.IntegerField()
    due_soon = serializers.IntegerField()
    awaiting_external = serializers.IntegerField()
    other = serializers.IntegerField()
    open = serializers.IntegerField()


class DashboardHomeSerializer(serializers.Serializer):
    # The response varies by the signed-in user's role.
    role = serializers.ChoiceField(choices=User.Role.choices)
    as_of = serializers.DateField()

    # Full dashboard response: MD, Manager and Viewer.
    attention = AttentionSummarySerializer(required=False)
    exposure = ExposureSummarySerializer(required=False)
    ageing = AgeingSerializer(required=False)
    by_estate = EstateDashboardRowSerializer(many=True, required=False)
    by_type = TypeDashboardRowSerializer(many=True, required=False)
    delay_reasons = DelayReasonSerializer(many=True, required=False)
    oldest = ObligationListSerializer(many=True, required=False)
    workload = WorkloadSerializer(required=False)
    cancelled_last_30_days = serializers.IntegerField(required=False)

    # Staff response.
    queue = StaffDashboardQueueSerializer(required=False)


class DashboardViewSet(viewsets.ViewSet):
    """Company-wide blocks need `view_dashboard` (MD, Manager, Viewer). `home` adapts to the caller's role."""
    permission_classes = [ActionPermission]
    required_actions = {
        "home": "view_home", "attention": "view_dashboard", "exposure": "view_dashboard", "ageing": "view_dashboard",
        "by_estate": "view_dashboard", "by_type": "view_dashboard", "delay_reasons": "view_dashboard",
        "oldest": "view_dashboard", "workload": "view_dashboard",
    }

    def _company(self, request):
        return request.user.company

    @extend_schema(responses=DashboardHomeSerializer)
    @action(detail=False, methods=["get"])
    def home(self, request):
        company, user = self._company(request), request.user
        today, now = _clock()
        base = {"role": user.role, "as_of": today.isoformat()}
        if user.role in FULL_DASHBOARD_ROLES:
            return Response({
                **base,
                "attention": reports.attention_summary(company, today, now),
                "exposure": exposure_payload(company, today),
                "ageing": services.ageing(company, today),
                "by_estate": _rows(reports.by_estate(company, today), "amount"),
                "by_type": _rows(reports.by_type(company, today), "amount"),
                "delay_reasons": reports.delay_reasons(company),
                "oldest": oldest_payload(request, company),
                "workload": workload_payload(company, today, now),
                "cancelled_last_30_days": reports.cancelled_recently(company, 30, now),
            })
        if user.role == "staff":
            groups = reports.queue_groups(user, today)
            return Response({**base, "queue": {k: len(v) for k, v in groups.items()} | {
                "open": sum(len(v) for v in groups.values())}})
        return Response(base)                                           # sales: works from the obligations list

    @extend_schema(responses=AttentionSummarySerializer)
    @action(detail=False, methods=["get"])
    def attention(self, request):
        today, now = _clock()
        return Response(reports.attention_summary(self._company(request), today, now))

    @extend_schema(responses=ExposureSummarySerializer)
    @action(detail=False, methods=["get"])
    def exposure(self, request):
        today, _ = _clock()
        return Response(exposure_payload(self._company(request), today))

    @extend_schema(responses=AgeingSerializer)
    @action(detail=False, methods=["get"])
    def ageing(self, request):
        today, _ = _clock()
        return Response(services.ageing(self._company(request), today))

    @extend_schema(responses=EstateDashboardRowSerializer(many=True))
    @action(detail=False, methods=["get"], url_path="by-estate", url_name="by-estate")
    def by_estate(self, request):
        today, _ = _clock()
        return Response(_rows(reports.by_estate(self._company(request), today), "amount"))

    @extend_schema(responses=TypeDashboardRowSerializer(many=True))
    @action(detail=False, methods=["get"], url_path="by-type", url_name="by-type")
    def by_type(self, request):
        today, _ = _clock()
        return Response(_rows(reports.by_type(self._company(request), today), "amount"))

    @extend_schema(responses=DelayReasonSerializer(many=True))
    @action(detail=False, methods=["get"], url_path="delay-reasons", url_name="delay-reasons")
    def delay_reasons(self, request):
        return Response(reports.delay_reasons(self._company(request)))

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="limit",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                required=False,
                description=(
                    "Maximum number of oldest open obligations to return. "
                    "Defaults to 10; values are clamped to 1–50."
                ),
            )
        ],
        responses=ObligationListSerializer(many=True),
    )
    @action(detail=False, methods=["get"])
    def oldest(self, request):
        raw = str(request.query_params.get("limit", "10"))
        limit = min(max(int(raw), 1), 50) if raw.isdigit() else 10
        return Response(oldest_payload(request, self._company(request), limit))

    @extend_schema(responses=WorkloadSerializer)
    @action(detail=False, methods=["get"])
    def workload(self, request):
        today, now = _clock()
        return Response(workload_payload(self._company(request), today, now))


class MyQueueView(APIView):
    """The signed-in person's own active work, grouped. Same rows and fields as /api/obligations/."""
    permission_classes = [ActionPermission]
    required_action = "view_my_queue"

    @extend_schema(
        responses={
            200: inline_serializer(
                name="MyQueueResponse",
                fields={
                    "counts": inline_serializer(
                        name="MyQueueCounts",
                        fields={
                            "overdue": serializers.IntegerField(),
                            "due_soon": serializers.IntegerField(),
                            "awaiting_external": serializers.IntegerField(),
                            "other": serializers.IntegerField(),
                            "open": serializers.IntegerField(),
                        },
                    ),
                    "overdue": ObligationListSerializer(many=True),
                    "due_soon": ObligationListSerializer(many=True),
                    "awaiting_external": ObligationListSerializer(many=True),
                    "other": ObligationListSerializer(many=True),
                },
            )
        }
    )
    def get(self, request):
        groups = reports.queue_groups(request.user)
        ctx = {"request": request}
        return Response({
            "counts": {k: len(v) for k, v in groups.items()} | {"open": sum(len(v) for v in groups.values())},
            **{k: ObligationListSerializer(v, many=True, context=ctx).data for k, v in groups.items()},
        })
