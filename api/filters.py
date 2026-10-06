from datetime import timedelta

from django.db.models import Q
from django.db.models.functions import Trim
from django.utils import timezone
from django_filters import rest_framework as df

from tracker.computed import STALE_RED_DAYS
from tracker.models import Obligation

OPEN = Q(status__in=Obligation.OPEN_STATUSES) & Q(sale__status="active")   # active work only


class CharInFilter(df.BaseInFilter, df.CharFilter):
    """?status=in_progress,awaiting_external"""


class ObligationFilter(df.FilterSet):
    status = CharInFilter(field_name="status", lookup_expr="in")
    estate = df.NumberFilter(field_name="sale__plot__estate_id")
    type = df.NumberFilter(field_name="obligation_type_id")
    owner = df.NumberFilter(field_name="owner_id")
    department = df.NumberFilter(field_name="department_id")
    external_party = df.CharFilter(lookup_expr="icontains")
    open = df.BooleanFilter(method="filter_open")
    overdue = df.BooleanFilter(method="filter_overdue")
    stale = df.BooleanFilter(method="filter_stale")
    incomplete = df.BooleanFilter(method="filter_incomplete")
    due_within = df.NumberFilter(method="filter_due_within")      # ?due_within=7 -> open, due today..7 days from now
    q = df.CharFilter(method="filter_q")

    class Meta:
        model = Obligation
        fields = []

    @staticmethod
    def _apply(qs, condition, value):
        # value True -> rows matching the condition; value False -> rows NOT matching it
        return qs.filter(condition) if value else qs.exclude(condition)

    def filter_open(self, qs, name, value):
        return self._apply(qs, OPEN, value)

    def filter_overdue(self, qs, name, value):
        return self._apply(qs, OPEN & Q(due_date__lt=timezone.localdate()), value)

    def filter_stale(self, qs, name, value):
        cutoff = timezone.now() - timedelta(days=STALE_RED_DAYS)
        return self._apply(qs, OPEN & Q(last_updated_at__lt=cutoff), value)

    def filter_due_within(self, qs, name, value):
        days = max(0, min(int(value), 365))
        today = timezone.localdate()
        return qs.filter(OPEN & Q(due_date__gte=today, due_date__lte=today + timedelta(days=days)))

    def filter_incomplete(self, qs, name, value):
        qs = qs.annotate(_next_action_trimmed=Trim("next_action"))
        return self._apply(qs, OPEN & (Q(owner__isnull=True) | Q(_next_action_trimmed="")), value)

    def filter_q(self, qs, name, value):
        value = (value or "").strip()
        if not value:
            return qs
        return qs.filter(
            Q(sale__buyer__full_name__icontains=value)
            | Q(sale__plot__plot_number__icontains=value)
            | Q(external_party__icontains=value)
        )
