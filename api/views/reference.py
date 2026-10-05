from rest_framework import viewsets

from api.mixins import CompanyScopedMixin
from api.serializers.reference import (
    BuyerSerializer, DepartmentSerializer, EstateSerializer,
    PlotSerializer, SaleSerializer, UserLiteSerializer,
)
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
