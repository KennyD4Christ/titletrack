import functools

from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.response import Response

from api.mixins import CompanyScopedMixin
from api.serializers.inventory import (
    BulkCommitSerializer, BulkPreviewSerializer, EstateCreateSerializer, EstateEditSerializer,
    PlotRegisterSerializer, PlotRemoveSerializer, PlotRenameSerializer,
)
from api.serializers.reference import EstateSerializer, PlotSerializer
from tracker import inventory_services as inv
from tracker.models import Estate, Plot


def _refusal(exc):
    """The same mapping as the rest of the API: 400 for bad input, 409 for a valid request the current state
    forbids. A 409 carries what the client needs to recover (the record that is in the way, the fresh preview)."""
    if isinstance(exc, inv.DuplicateEstate):
        body = {"detail": str(exc), "existing_estate": {"id": exc.estate.pk, "name": exc.estate.name}}
    elif isinstance(exc, inv.DuplicatePlot):
        body = {"detail": str(exc), "existing_plot": {"id": exc.plot.pk, "plot_number": exc.plot.plot_number}}
    elif isinstance(exc, inv.StalePreview):
        body = {"detail": str(exc), "preview": exc.preview}
    elif isinstance(exc, inv.PlotCapExceeded):
        body = {"detail": str(exc), "limit": exc.limit, "existing": exc.existing, "requested": exc.requested}
    elif isinstance(exc, inv.InventoryConflict):
        body = {"detail": str(exc)}
    else:
        return Response({"detail": str(exc)}, status=400)
    return Response(body, status=409)


def refusals(method):
    """Turn the service's refusals into responses, and a record that vanished between the lookup and the service
    (another person removed it) into the same 404 an unknown id gets."""

    @functools.wraps(method)
    def wrapper(self, request, *args, **kwargs):
        try:
            return method(self, request, *args, **kwargs)
        except (ValueError, inv.InventoryConflict) as exc:
            return _refusal(exc)
        except (Estate.DoesNotExist, Plot.DoesNotExist):
            raise NotFound() from None

    return wrapper


class _InventoryViewSet(CompanyScopedMixin, viewsets.GenericViewSet):
    """MD and Manager only (manage_inventory). Every write goes through tracker/inventory_services.py. Company
    scoped, so another company's record is a 404. Writes only: reading estates and plots stays with the read-only
    /api/estates/ and /api/plots/, which are unchanged."""
    http_method_names = ["post", "patch", "head", "options"]

    def _input(self, serializer_class):
        serializer = serializer_class(data=self.request.data, context={"request": self.request})
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data


class InventoryEstateViewSet(_InventoryViewSet):
    queryset = Estate.objects.all()
    required_actions = {"create": "manage_inventory", "partial_update": "manage_inventory"}

    @refusals
    def create(self, request, *args, **kwargs):
        data = self._input(EstateCreateSerializer)
        estate = inv.create_estate(request.user.company, request.user, name=data["name"],
                                   location=data.get("location", ""))
        return Response(EstateSerializer(estate).data, status=201)

    @refusals
    def partial_update(self, request, *args, **kwargs):
        estate = self.get_object()
        data = self._input(EstateEditSerializer)
        edited = inv.edit_estate(estate, request.user, name=data.get("name"), location=data.get("location"),
                                 reason=data.get("reason", ""))
        return Response(EstateSerializer(edited).data)


class InventoryPlotViewSet(_InventoryViewSet):
    queryset = Plot.objects.select_related("estate")
    required_actions = {
        "create": "manage_inventory", "partial_update": "manage_inventory", "remove": "manage_inventory",
        "bulk_preview": "manage_inventory", "bulk_commit": "manage_inventory",
    }

    @refusals
    def create(self, request, *args, **kwargs):
        data = self._input(PlotRegisterSerializer)
        plot = inv.register_plot(request.user.company, request.user, estate=data["estate"],
                                 plot_number=data["plot_number"])
        return Response(PlotSerializer(plot).data, status=201)

    @refusals
    def partial_update(self, request, *args, **kwargs):
        """Rename. A plot that has ever had a sale needs a reason."""
        plot = self.get_object()
        data = self._input(PlotRenameSerializer)
        renamed = inv.rename_plot(plot, request.user, plot_number=data["plot_number"], reason=data.get("reason", ""))
        return Response(PlotSerializer(renamed).data)

    @action(detail=True, methods=["post"])
    @refusals
    def remove(self, request, *args, **kwargs):
        """Remove a plot that was registered by mistake: only one that never had a sale (cancelled sales count)."""
        plot = self.get_object()
        data = self._input(PlotRemoveSerializer)
        inv.remove_plot(plot, request.user, reason=data["reason"])
        return Response({"id": plot.pk, "plot_number": plot.plot_number, "removed": True})

    @action(detail=False, methods=["post"], url_path="bulk-preview", url_name="bulk-preview")
    @refusals
    def bulk_preview(self, request, *args, **kwargs):
        data = self._input(BulkPreviewSerializer)
        return Response(inv.preview_bulk(request.user.company, request.user, estate=data["estate"],
                                         entries=data["entries"]))

    @action(detail=False, methods=["post"], url_path="bulk-commit", url_name="bulk-commit")
    @refusals
    def bulk_commit(self, request, *args, **kwargs):
        """Register the new labels of a previewed request, all or none. The two fingerprints the preview returned
        must be sent back; a stale preview is a 409 that carries the fresh one."""
        data = self._input(BulkCommitSerializer)
        result = inv.commit_bulk(
            request.user.company, request.user, estate=data["estate"], entries=data["entries"],
            input_fingerprint_sent=data["input_fingerprint"], preview_fingerprint_sent=data["preview_fingerprint"])
        return Response({
            "created": len(result.created),
            "plots": [{"id": plot.pk, "plot_number": plot.plot_number} for plot in result.created],
            "skipped_existing": result.skipped_existing,
            "skipped_repeated": result.skipped_repeated,
        }, status=201)
