from datetime import timedelta
from decimal import Decimal

from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from api.permissions import ActionPermission
from api.serializers.obligations import ObligationListSerializer
from tracker import reports, services

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

    @action(detail=False, methods=["get"])
    def attention(self, request):
        today, now = _clock()
        return Response(reports.attention_summary(self._company(request), today, now))

    @action(detail=False, methods=["get"])
    def exposure(self, request):
        today, _ = _clock()
        return Response(exposure_payload(self._company(request), today))

    @action(detail=False, methods=["get"])
    def ageing(self, request):
        today, _ = _clock()
        return Response(services.ageing(self._company(request), today))

    @action(detail=False, methods=["get"], url_path="by-estate", url_name="by-estate")
    def by_estate(self, request):
        today, _ = _clock()
        return Response(_rows(reports.by_estate(self._company(request), today), "amount"))

    @action(detail=False, methods=["get"], url_path="by-type", url_name="by-type")
    def by_type(self, request):
        today, _ = _clock()
        return Response(_rows(reports.by_type(self._company(request), today), "amount"))

    @action(detail=False, methods=["get"], url_path="delay-reasons", url_name="delay-reasons")
    def delay_reasons(self, request):
        return Response(reports.delay_reasons(self._company(request)))

    @action(detail=False, methods=["get"])
    def oldest(self, request):
        raw = str(request.query_params.get("limit", "10"))
        limit = min(max(int(raw), 1), 50) if raw.isdigit() else 10
        return Response(oldest_payload(request, self._company(request), limit))

    @action(detail=False, methods=["get"])
    def workload(self, request):
        today, now = _clock()
        return Response(workload_payload(self._company(request), today, now))


class MyQueueView(APIView):
    """The signed-in person's own active work, grouped. Same rows and fields as /api/obligations/."""
    permission_classes = [ActionPermission]
    required_action = "view_my_queue"

    def get(self, request):
        groups = reports.queue_groups(request.user)
        ctx = {"request": request}
        return Response({
            "counts": {k: len(v) for k, v in groups.items()} | {"open": sum(len(v) for v in groups.values())},
            **{k: ObligationListSerializer(v, many=True, context=ctx).data for k, v in groups.items()},
        })
