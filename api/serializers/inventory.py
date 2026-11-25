"""Input for the inventory endpoints. Text is taken as sent (nothing trimmed here) and checked by
tracker/inventory_services.py, the one place with the rules, so a rule and its message live once. These
serializers only check shape. An estate id is a choice among the CALLER'S company's estates, so another company's
estate simply does not exist as a valid value."""
from rest_framework import serializers

from tracker.models import Estate

_text = dict(trim_whitespace=False, allow_blank=True)


class EstateCreateSerializer(serializers.Serializer):
    name = serializers.CharField(**_text)
    location = serializers.CharField(required=False, **_text)


class EstateEditSerializer(serializers.Serializer):
    name = serializers.CharField(required=False, **_text)
    location = serializers.CharField(required=False, **_text)
    reason = serializers.CharField(required=False, **_text)


class _InEstate(serializers.Serializer):
    estate = serializers.PrimaryKeyRelatedField(queryset=Estate.objects.none())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["estate"].queryset = Estate.objects.filter(company_id=self.context["request"].user.company_id)


class PlotRegisterSerializer(_InEstate):
    plot_number = serializers.CharField(**_text)


class PlotRenameSerializer(serializers.Serializer):
    plot_number = serializers.CharField(**_text)
    reason = serializers.CharField(required=False, **_text)


class PlotRemoveSerializer(serializers.Serializer):
    reason = serializers.CharField(**_text)


class BulkPreviewSerializer(_InEstate):
    # Each entry is {"labels": [...]} or a range {"prefix", "start", "end", "pad", "suffix"}; the service checks it.
    entries = serializers.ListField(child=serializers.DictField(child=serializers.JSONField()), allow_empty=True)


class BulkCommitSerializer(BulkPreviewSerializer):
    input_fingerprint = serializers.CharField(**_text)
    preview_fingerprint = serializers.CharField(**_text)
