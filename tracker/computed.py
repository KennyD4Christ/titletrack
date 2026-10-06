"""Pure functions that describe an obligation relative to 'today' / 'now'.
Used by the API (and later the dashboards) so every screen uses the same definitions."""
from datetime import timedelta

from .models import Obligation, Sale

# "Last updated" colour bands. RED matches services.stale(): not updated for MORE than 14 days.
STALE_AMBER_DAYS = 7
STALE_RED_DAYS = 14


def is_cancelled(ob):
    return ob.sale.status == Sale.Status.CANCELLED


def is_open(ob):
    """Active work: an open obligation on an active sale (obligations of cancelled sales are never 'open')."""
    return ob.status in Obligation.OPEN_STATUSES and ob.sale.status == Sale.Status.ACTIVE


def days_outstanding(ob, today):
    return (today - ob.started_on).days if is_open(ob) else None


def days_overdue(ob, today):
    if is_open(ob) and ob.due_date and today > ob.due_date:
        return (today - ob.due_date).days
    return 0


def staleness(ob, now):
    """'green' / 'amber' / 'red' for open obligations, None otherwise."""
    if not is_open(ob):
        return None
    if ob.last_updated_at < now - timedelta(days=STALE_RED_DAYS):
        return "red"
    if ob.last_updated_at < now - timedelta(days=STALE_AMBER_DAYS):
        return "amber"
    return "green"


def is_incomplete(ob):
    """Open, and missing an owner or a next action."""
    return is_open(ob) and (ob.owner_id is None or not (ob.next_action or "").strip())
