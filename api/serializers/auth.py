from rest_framework import serializers

from tracker.models import Company, Department, User


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(trim_whitespace=False)


class _CompanySerializer(serializers.ModelSerializer):
    class Meta:
        model = Company
        fields = ["id", "name"]


class _DepartmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Department
        fields = ["id", "name"]


class MeSerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField()
    company = _CompanySerializer(read_only=True)
    department = _DepartmentSerializer(read_only=True, allow_null=True)

    class Meta:
        model = User
        fields = ["id", "username", "full_name", "role", "company", "department"]

    def get_full_name(self, obj):
        return obj.get_full_name() or obj.username
