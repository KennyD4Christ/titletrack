from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from api.mixins import CompanyScopedMixin
from api.serializers.reference import (
    BuyerSerializer, DepartmentSerializer, EstateSerializer,
    PlotSerializer, SaleSerializer, UserLiteSerializer,
)
from tracker import services
from tracker.models import Buyer, Department, Estate, Plot, Sale, User


class ReferenceViewSet(CompanyScopedMixin, viewsets.ReadOnlyModelViewSet):
    required_action = "view_reference"


class EstateViewSet(ReferenceViewSet):
    queryset = Estate.objects.order_by("name")
    serializer_class = EstateSerializer


class PlotViewSet(ReferenceViewSet):
    queryset = Plot.objects.select_related("estate").order_by("estate__name", "plot_number")
    serializer_class = PlotSerializer
    filterset_fields = ["estate"]


class BuyerViewSet(ReferenceViewSet):
    queryset = Buyer.objects.order_by("full_name")
    serializer_class = BuyerSerializer


class DepartmentViewSet(ReferenceViewSet):
    queryset = Department.objects.order_by("name")
    serializer_class = DepartmentSerializer


class UserViewSet(ReferenceViewSet):
    queryset = User.objects.filter(is_active=True).order_by("username")
    serializer_class = UserLiteSerializer


class SaleViewSet(ReferenceViewSet):
    queryset = Sale.objects.select_related("plot", "buyer").order_by("-sale_date", "-id")
    serializer_class = SaleSerializer
    filterset_fields = ["status", "plot__estate"]
    required_actions = {
        "list": "view_reference", "retrieve": "view_reference",
        "cancel": "manage_sales", "reinstate": "manage_sales",
    }

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        sale = self.get_object()
        try:
            services.cancel_sale(sale, request.user, request.data.get("reason"))
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)
        except services.SaleStateError as exc:
            return Response({"detail": str(exc)}, status=409)
        sale.refresh_from_db()
        return Response(SaleSerializer(sale).data)

    @action(detail=True, methods=["post"])
    def reinstate(self, request, pk=None):
        sale = self.get_object()
        try:
            services.reinstate_sale(sale, request.user, request.data.get("note") or "")
        except services.SaleStateError as exc:
            return Response({"detail": str(exc)}, status=409)
        sale.refresh_from_db()
        return Response(SaleSerializer(sale).data)
