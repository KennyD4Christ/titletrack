from django.db.models import Count, F, Q
from django_filters import rest_framework as df
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.response import Response

from api.mixins import CompanyScopedMixin
from api.serializers.sales import (
    BuyerEditSerializer, CancelSerializer, PaymentCreateSerializer, PaymentSerializer, ReinstateSerializer,
    ReversePaymentSerializer, SaleCreateSerializer, SaleDeskDetailSerializer, SaleDeskListSerializer,
)
from tracker import services
from tracker.models import Buyer, Obligation, Payment, Sale


class SaleDeskFilter(df.FilterSet):
    status = df.ChoiceFilter(field_name="status", choices=Sale.Status.choices)
    estate = df.NumberFilter(field_name="plot__estate_id")
    fully_paid = df.BooleanFilter(method="filter_fully_paid")
    q = df.CharFilter(method="filter_q")

    class Meta:
        model = Sale
        fields = []

    def filter_fully_paid(self, qs, name, value):
        return qs.filter(amount_paid__gte=F("price")) if value else qs.filter(amount_paid__lt=F("price"))

    def filter_q(self, qs, name, value):
        value = (value or "").strip()
        if not value:
            return qs
        return qs.filter(
            Q(buyer__full_name__icontains=value) | Q(plot__plot_number__icontains=value)
            | Q(buyer__phone__icontains=value))


def _refusal(exc):
    """The same mapping the rest of the API uses: 409 for 'wrong state', 400 for bad input."""
    if isinstance(exc, services.PlotOccupied):
        sale = exc.sale
        return Response({"detail": str(exc),
                         "existing_sale": {"id": sale.pk, "buyer": sale.buyer_id, "buyer_name": sale.buyer.full_name}},
                        status=409)
    if isinstance(exc, services.SaleStateError):
        return Response({"detail": str(exc)}, status=409)
    return Response({"detail": str(exc)}, status=400)


def _created(obligations):
    return [{"id": o.pk, "type_name": o.obligation_type.name, "started_on": o.started_on.isoformat(),
             "due_date": o.due_date.isoformat() if o.due_date else None} for o in obligations]


class SalesDeskViewSet(CompanyScopedMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """The Sales desk: MD and Manager only. Every write goes through tracker/services.py. Company-scoped, so
    another company's sale is a 404. The read-only /api/sales/ route is unchanged."""
    queryset = (
        Sale.objects.select_related("plot__estate", "buyer")
        .annotate(
            obligations_total=Count("obligations", distinct=True),
            obligations_open=Count("obligations", filter=Q(obligations__status__in=Obligation.OPEN_STATUSES),
                                   distinct=True))
        .order_by("-sale_date", "-id"))
    filterset_class = SaleDeskFilter
    ordering_fields = ["sale_date", "price", "amount_paid", "id"]
    http_method_names = ["get", "post", "head", "options"]
    required_actions = {
        "list": "view_sales", "retrieve": "view_sales", "buyer_search": "add_sale", "create": "add_sale",
        "record_payment": "record_payment", "reverse_payment": "reverse_payment", "edit_buyer": "edit_buyer",
        "cancel": "manage_sales", "reinstate": "manage_sales",
    }

    def get_serializer_class(self):
        return SaleDeskListSerializer if self.action == "list" else SaleDeskDetailSerializer

    def _detail(self, pk):
        fresh = self.get_queryset().get(pk=pk)
        return dict(SaleDeskDetailSerializer(fresh, context=self.get_serializer_context()).data)

    def _input(self, serializer_class):
        serializer = serializer_class(data=self.request.data, context={"request": self.request})
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data

    # ---- reads ----

    @action(detail=False, methods=["get"], url_path="buyer-search", url_name="buyer-search")
    def buyer_search(self, request):
        """Find an existing buyer to reuse when adding a sale (name, phone or email; at least 2 characters)."""
        q = (request.query_params.get("q") or "").strip()
        if len(q) < 2:
            return Response({"results": []})
        rows = (Buyer.objects.filter(company_id=request.user.company_id)
                .filter(Q(full_name__icontains=q) | Q(phone__icontains=q) | Q(email__icontains=q))
                .annotate(sales_count=Count("sales")).order_by("full_name", "id")[:20])
        return Response({"results": [{"id": b.pk, "full_name": b.full_name, "phone": b.phone, "email": b.email,
                                      "sales_count": b.sales_count} for b in rows]})

    # ---- writes ----

    def create(self, request, *args, **kwargs):
        data = self._input(SaleCreateSerializer)
        try:
            sale = services.add_sale(
                request.user.company, request.user, plot_number=data["plot_number"], price=data["price"],
                sale_date=data["sale_date"], estate=data.get("estate"), estate_name=data.get("estate_name", ""),
                buyer=data.get("buyer"), buyer_name=data.get("buyer_name", ""), phone=data.get("phone", ""),
                email=data.get("email", ""), amount_paid=data.get("amount_paid", "0"), paid_on=data.get("paid_on"))
        except (ValueError, services.SaleStateError) as exc:
            return _refusal(exc)
        return Response(self._detail(sale.pk), status=201)

    @action(detail=True, methods=["post"], url_path="record-payment", url_name="record-payment")
    def record_payment(self, request, pk=None):
        sale = self.get_object()
        data = self._input(PaymentCreateSerializer)
        try:
            result = services.record_payment(
                sale, request.user, data["amount"], paid_on=data.get("paid_on"), reference=data.get("reference", ""),
                note=data.get("note", ""), idempotency_key=data["idempotency_key"])
        except (ValueError, services.SaleStateError) as exc:
            return _refusal(exc)
        body = self._detail(sale.pk)
        body["payment"] = PaymentSerializer(result.payment).data
        body["obligations_created"] = _created(result.obligations_created)
        body["duplicate"] = result.duplicate
        return Response(body, status=200 if result.duplicate else 201)

    @action(detail=True, methods=["post"], url_path=r"payments/(?P<payment_id>[0-9]{1,18})/reverse",
            url_name="reverse-payment")
    def reverse_payment(self, request, pk=None, payment_id=None):
        sale = self.get_object()
        payment = Payment.objects.filter(sale_id=sale.pk, pk=payment_id).first()
        if payment is None:
            raise NotFound("Payment not found.")
        data = self._input(ReversePaymentSerializer)
        try:
            reversal = services.reverse_payment(payment, request.user, data["reason"])
        except (ValueError, services.SaleStateError) as exc:
            return _refusal(exc)
        body = self._detail(sale.pk)
        body["reversal"] = PaymentSerializer(reversal).data
        return Response(body)

    @action(detail=True, methods=["post"], url_path="edit-buyer", url_name="edit-buyer")
    def edit_buyer(self, request, pk=None):
        sale = self.get_object()
        data = self._input(BuyerEditSerializer)
        try:
            services.edit_buyer(sale, request.user, full_name=data.get("full_name"), phone=data.get("phone"),
                                email=data.get("email"))
        except (ValueError, services.SaleStateError) as exc:
            return _refusal(exc)
        return Response(self._detail(sale.pk))

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        sale = self.get_object()
        data = self._input(CancelSerializer)
        try:
            services.cancel_sale(sale, request.user, data["reason"])
        except (ValueError, services.SaleStateError) as exc:
            return _refusal(exc)
        return Response(self._detail(sale.pk))

    @action(detail=True, methods=["post"])
    def reinstate(self, request, pk=None):
        sale = self.get_object()
        data = self._input(ReinstateSerializer)
        try:
            result = services.reinstate_sale(sale, request.user, data.get("note", ""))
        except (ValueError, services.SaleStateError) as exc:
            return _refusal(exc)
        body = self._detail(sale.pk)
        body["obligations_created"] = _created(result.obligations_created)
        return Response(body)
