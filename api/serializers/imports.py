from rest_framework import serializers
from drf_spectacular.utils import extend_schema_field

from tracker.models import ImportBatch


class UploadSerializer(serializers.Serializer):
    file = serializers.FileField()
    sheet = serializers.CharField(required=False, allow_blank=True)
    header_row = serializers.IntegerField(required=False, min_value=1, max_value=1000)

class ImportSummarySerializer(serializers.Serializer):
    total_rows = serializers.IntegerField()
    to_import = serializers.IntegerField()
    unchanged = serializers.IntegerField()
    conflicts = serializers.IntegerField()
    errors = serializers.IntegerField()
    warnings = serializers.IntegerField()
    blank_rows = serializers.IntegerField()
    new_estates = serializers.ListField(
        child=serializers.CharField()
    )
    buyers_to_create = serializers.IntegerField()
    can_commit = serializers.BooleanField()

class ImportBatchSerializer(serializers.ModelSerializer):
    created_by_name = serializers.SerializerMethodField()
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    summary = serializers.SerializerMethodField()

    class Meta:
        model = ImportBatch
        fields = [
            "id", "filename", "sheet_name", "sheets", "status", "status_label", "row_count", "headers",
            "mapping", "status_mapping", "summary", "counts", "created_by_name",
            "created_at", "committed_at", "undone_at",
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_created_by_name(self, obj):
        user = obj.created_by
        return (user.get_full_name() or user.username) if user else None

    @extend_schema_field(ImportSummarySerializer(allow_null=True))
    def get_summary(self, obj):
        return (obj.report or {}).get("summary")
