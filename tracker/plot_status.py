"""What state is each plot in? Derived, never stored.

A plot's state comes only from its Sale rows, by the rule the database itself enforces (the partial unique
constraint one_active_sale_per_plot):
  - no active sale on the plot          -> available
  - an active sale                      -> sold, and the sale is part paid or fully paid
A cancelled sale frees the plot, so it counts for nothing here. There is no status column on Plot, no cache and no
manual flag, so there is no second source of truth that can drift from the sales.

Fully paid means amount_paid >= price, exactly as Sale.is_fully_paid and the Sales desk say. It is not read from
fully_paid_on: reverse_payment lowers amount_paid but leaves fully_paid_on, so that column can say "paid in full"
for a sale that no longer is.

Company isolation is part of the query, not left to the caller. A plot is only listed under an estate of its own
company, and a sale counts only if it belongs to the same company as the plot. A sale that somehow carries another
company's id (the database does not forbid it) is therefore ignored, never revealed on that company's board.

One query for a whole estate, however many plots it has.
"""
from django.conf import settings
from django.db.models import Exists, F, OuterRef, Subquery

from .models import Plot, Sale

AVAILABLE = "available"
SOLD = "sold"
PART_PAID = "part_paid"
FULLY_PAID = "fully_paid"

DEFAULT_BOARD_MAX_PLOTS = 5000


def board_max_plots():
    """A safety ceiling on one board. Registration is capped far below it (INVENTORY_MAX_PLOTS_PER_ESTATE), but
    sales and imports create plots without that cap, so the board protects itself."""
    return int(getattr(settings, "INVENTORY_BOARD_MAX_PLOTS", DEFAULT_BOARD_MAX_PLOTS))


def active_sales_of_the_plots_company():
    """The active sales that count for a plot (to be used inside a Plot query): same plot, same company."""
    return Sale.objects.filter(plot=OuterRef("pk"), company_id=OuterRef("company_id"), status=Sale.Status.ACTIVE)


def plots_with_status(estate):
    """The estate's plots, each annotated with `active_sale_id` (None when available) and `fully_paid`, in a
    stable order (canonical key, then id). Only plots of the estate's own company are listed."""
    active = active_sales_of_the_plots_company()
    return (Plot.objects.filter(estate=estate, company_id=estate.company_id)
            .annotate(active_sale_id=Subquery(active.values("pk")[:1]),
                      fully_paid=Exists(active.filter(amount_paid__gte=F("price"))))
            .order_by("plot_key", "pk"))


def status_of(plot):
    """AVAILABLE or SOLD, for a plot from plots_with_status."""
    return SOLD if plot.active_sale_id is not None else AVAILABLE


def payment_state_of(plot):
    """PART_PAID or FULLY_PAID for a sold plot; None for an available one."""
    if plot.active_sale_id is None:
        return None
    return FULLY_PAID if plot.fully_paid else PART_PAID
