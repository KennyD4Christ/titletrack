"""The plot board: one row per plot. Two serializers, chosen by the caller's permission, and never one serializer
with fields taken away: the public one cannot leak what it does not have.

Public (every role): id, plot_number, status (available or sold).
Sales-capable (roles with view_sales, today MD and Manager): the same plus payment_state and active_sale_id, the
sale to open. Neither contains a buyer, a price, an amount or a date: those come only from the Sales desk's own
endpoint, which enforces its own permission. Tests pin the exact key sets."""
from rest_framework import serializers

from tracker import plot_status


class PlotBoardPublicSerializer(serializers.Serializer):
    """Reads a Plot from tracker.plot_status.plots_with_status."""
    id = serializers.IntegerField(read_only=True)
    plot_number = serializers.CharField(read_only=True)
    status = serializers.SerializerMethodField()

    def get_status(self, plot):
        return plot_status.status_of(plot)


class PlotBoardSalesSerializer(PlotBoardPublicSerializer):
    payment_state = serializers.SerializerMethodField()
    active_sale_id = serializers.IntegerField(read_only=True)

    def get_payment_state(self, plot):
        return plot_status.payment_state_of(plot)
