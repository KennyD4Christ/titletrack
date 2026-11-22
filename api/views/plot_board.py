from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from api.permissions import ActionPermission, can
from api.serializers.plot_board import PlotBoardPublicSerializer, PlotBoardSalesSerializer
from api.serializers.reference import EstateSerializer
from tracker import plot_status
from tracker.models import Estate, Plot

_datetime = serializers.DateTimeField().to_representation


class PlotBoardView(APIView):
    """GET /api/plot-board/?estate=<id>: every plot of one estate with its derived state. One query for the plots,
    whatever their number. Every role may read it; what a role sees beyond plot number and available/sold depends
    on view_sales. Another company's estate does not exist for the caller (404)."""
    permission_classes = [ActionPermission]
    required_action = "view_plot_board"

    def get(self, request):
        raw = (request.query_params.get("estate") or "").strip()
        if not raw:
            raise ValidationError({"estate": ["This query parameter is required."]})
        if not raw.isdigit() or len(raw) > 18:
            raise ValidationError({"estate": ["Use a whole number."]})
        estate = Estate.objects.filter(company_id=request.user.company_id, pk=int(raw)).first()
        if estate is None:
            raise NotFound()

        limit = plot_status.board_max_plots()
        rows = list(plot_status.plots_with_status(estate)[: limit + 1])
        truncated = len(rows) > limit
        rows = rows[:limit]
        count = Plot.objects.filter(estate=estate, company_id=estate.company_id).count() if truncated else len(rows)

        serializer = PlotBoardSalesSerializer if can(request.user, "view_sales") else PlotBoardPublicSerializer
        response = Response({
            "estate": EstateSerializer(estate).data,
            "as_of": _datetime(timezone.now()),
            "count": count,
            "truncated": truncated,
            "plots": serializer(rows, many=True).data,
        })
        response["Cache-Control"] = "private, no-cache"          # live, permission-dependent data
        return response
