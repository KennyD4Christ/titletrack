from rest_framework import serializers

from tracker.document_services import (
    CATEGORY_LIMIT, DESCRIPTION_LIMIT, NAME_LIMIT, SLA_MAX, SLA_MIN, SORT_ORDER_MAX,
)
from tracker.models import Department


class DocumentTypeSerializer(serializers.Serializer):
    """One catalogue entry. Needs a queryset that prefetches the current work type into `current_types`
    (see DocumentTypeViewSet), so a list costs a fixed number of queries."""
    id = serializers.IntegerField(read_only=True)
    name = serializers.CharField(read_only=True)
    description = serializers.CharField(read_only=True)
    category = serializers.CharField(read_only=True)
    is_active = serializers.BooleanField(read_only=True)
    sort_order = serializers.IntegerField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    work_type = serializers.SerializerMethodField()
    sla_days = serializers.SerializerMethodField()
    default_department = serializers.SerializerMethodField()
    drives_legacy_generation = serializers.SerializerMethodField()

    def _current(self, obj):
        current = getattr(obj, "current_types", None)
        return current[0] if current else None

    def get_work_type(self, obj):
        current = self._current(obj)
        return current.pk if current else None

    def get_sla_days(self, obj):
        current = self._current(obj)
        return current.sla_days if current else None

    def get_default_department(self, obj):
        current = self._current(obj)
        return current.default_department_id if current else None

    def get_drives_legacy_generation(self, obj):
        """True for an entry that came from an existing automatic type: that type still creates obligations
        on legacy sales and is still offered for spreadsheet mapping (managed in the old settings)."""
        current = self._current(obj)
        return bool(current and current.auto_generate)


class _CompanyDepartmentMixin:
    def _limit_departments(self):
        request = self.context.get("request")
        if request is not None:
            self.fields["default_department"].queryset = Department.objects.filter(
                company_id=request.user.company_id)


class DocumentTypeCreateSerializer(_CompanyDepartmentMixin, serializers.Serializer):
    name = serializers.CharField(max_length=NAME_LIMIT)
    description = serializers.CharField(required=False, allow_blank=True, max_length=DESCRIPTION_LIMIT)
    category = serializers.CharField(required=False, allow_blank=True, max_length=CATEGORY_LIMIT)
    sort_order = serializers.IntegerField(required=False, min_value=0, max_value=SORT_ORDER_MAX)
    sla_days = serializers.IntegerField(required=False, min_value=SLA_MIN, max_value=SLA_MAX)
    default_department = serializers.PrimaryKeyRelatedField(
        queryset=Department.objects.none(), required=False, allow_null=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._limit_departments()


class DocumentTypeUpdateSerializer(_CompanyDepartmentMixin, serializers.Serializer):
    """The ONLY fields a client may change here. The name has its own action; anything else is ignored."""
    description = serializers.CharField(required=False, allow_blank=True, max_length=DESCRIPTION_LIMIT)
    category = serializers.CharField(required=False, allow_blank=True, max_length=CATEGORY_LIMIT)
    sort_order = serializers.IntegerField(required=False, min_value=0, max_value=SORT_ORDER_MAX)
    sla_days = serializers.IntegerField(required=False, min_value=SLA_MIN, max_value=SLA_MAX)
    default_department = serializers.PrimaryKeyRelatedField(
        queryset=Department.objects.none(), required=False, allow_null=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._limit_departments()

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError("Nothing to update.")
        return attrs


class DocumentTypeRenameSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=NAME_LIMIT)
