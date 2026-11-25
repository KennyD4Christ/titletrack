from django.db.models import Count, F, Q
from django_filters import rest_framework as df
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.response import Response

from api.mixins import CompanyScopedMixin
from api.permissions import can
from api.serializers.sales import (
    BuyerEditSerializer, CancelSerializer, PaymentCreateSerializer, PaymentSerializer, ReinstateSerializer,
    ReversePaymentSerializer, SaleCreateSerializer, SaleDeskDetailSerializer, SaleDeskListSerializer,
)
from tracker import sale_document_services as sale_documents
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


def _documents_refusal(exc):
    """A refused document, answered the way DRF answers a bad item in a list: {"documents": {"<position>": {field:
    [message]}}}, so it looks exactly like the serializer's own errors and a form can show the message beside
    the right row. 409 when the document is in the wrong state (for example an inactive type), 400 when its
    input breaks a rule."""
    problem = {exc.field or "non_field_errors": [str(exc.cause)]}
    return Response({"documents": {exc.index: problem}}, status=409 if exc.is_state_error else 400)


def _require_document_permissions(user, documents):
    """Choosing documents while adding a sale needs more than add_sale (the role matrix itself is unchanged):
    manage_sale_documents for any selection, and issue_document as well when any document is already issued.
    `documents` is the raw value (checked first, so nobody learns anything about a request they may not make) or
    the validated list (checked again). Nothing, null and an empty list need only add_sale."""
    if not documents:
        return
    if not can(user, "manage_sale_documents"):
        raise PermissionDenied("You do not have permission to add documents to a sale.")
    items = documents if isinstance(documents, list) else []
    issued = any(isinstance(item, dict) and item.get("state") == "issued" for item in items)
    if issued and not can(user, "issue_document"):
        raise PermissionDenied("You do not have permission to record a document as already issued.")


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
        raw_documents = request.data.get("documents") if hasattr(request.data, "get") else None
        _require_document_permissions(request.user, raw_documents)
        data = self._input(SaleCreateSerializer)
        documents = data.get("documents")
        _require_document_permissions(request.user, documents)
        if documents:
            return self._create_with_documents(request, data, documents)
        try:
            sale = services.add_sale(
                request.user.company, request.user, plot_number=data["plot_number"], price=data["price"],
                sale_date=data["sale_date"], estate=data.get("estate"), estate_name=data.get("estate_name", ""),
                buyer=data.get("buyer"), buyer_name=data.get("buyer_name", ""), phone=data.get("phone", ""),
                email=data.get("email", ""), amount_paid=data.get("amount_paid", "0"), paid_on=data.get("paid_on"),
                require_registered_plot=True,
            )
        except (ValueError, services.SaleStateError) as exc:
            return _refusal(exc)
        return Response(self._detail(sale.pk), status=201)

    def _create_with_documents(self, request, data, documents):
        """A sale created together with its chosen documents, in ONE transaction (see
        tracker/sale_document_services.add_sale_with_documents). The answer is the usual sale detail."""
        try:
            sale = sale_documents.add_sale_with_documents(
                request.user.company, request.user, documents, plot_number=data["plot_number"],
                price=data["price"], sale_date=data["sale_date"], estate=data.get("estate"),
                estate_name=data.get("estate_name", ""), buyer=data.get("buyer"),
                buyer_name=data.get("buyer_name", ""), phone=data.get("phone", ""), email=data.get("email", ""),
                amount_paid=data.get("amount_paid", "0"), paid_on=data.get("paid_on"),
                require_registered_plot=True,
            )
        except sale_documents.SaleDocumentsError as exc:
            return _documents_refusal(exc)
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
