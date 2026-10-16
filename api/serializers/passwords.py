from rest_framework import serializers


class LinkPasswordSerializer(serializers.Serializer):
    """Body of the public set-password / reset-password calls. The token is validated by the service, so a
    bad token always gets the same generic answer (no length or format hints)."""
    token = serializers.CharField(trim_whitespace=True)
    password = serializers.CharField(trim_whitespace=False, max_length=1024)


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(trim_whitespace=False, max_length=1024)
    new_password = serializers.CharField(trim_whitespace=False, max_length=1024)
