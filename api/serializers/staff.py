from rest_framework import serializers

from tracker import staff_services
from tracker.models import AccountEvent, Department, User


class StaffSerializer(serializers.ModelSerializer):
    """One account as an administrator sees it. Never holds a password, token or link."""
    full_name = serializers.SerializerMethodField()
    department_name = serializers.CharField(source="department.name", read_only=True, allow_null=True)
    open_obligations = serializers.SerializerMethodField()
    is_protected = serializers.SerializerMethodField()
    can_manage = serializers.SerializerMethodField()
    is_self = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id", "username", "email", "first_name", "last_name", "full_name", "role", "department",
            "department_name", "is_active", "last_login", "open_obligations", "is_protected", "can_manage", "is_self",
        ]
        read_only_fields = fields

    def get_full_name(self, obj):
        return obj.get_full_name() or obj.username

    def get_open_obligations(self, obj):
        count = getattr(obj, "open_obligations", None)          # annotated on lists; computed for a single object
        return count if count is not None else staff_services.open_obligation_count(obj)

    def get_is_protected(self, obj):
        return bool(obj.is_staff or obj.is_superuser)

    def get_can_manage(self, obj):
        return staff_services.may_manage(self.context["request"].user, obj)

    def get_is_self(self, obj):
        return obj.pk == self.context["request"].user.pk


class _DepartmentScoped(serializers.Serializer):
    def _scope_departments(self):
        company_id = self.context["request"].user.company_id
        self.fields["department"].queryset = Department.objects.filter(company_id=company_id)


class StaffCreateSerializer(_DepartmentScoped):
    """The company is never read from the request: it is always the signed-in administrator's company."""
    email = serializers.CharField(max_length=254)
    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150)
    role = serializers.ChoiceField(choices=User.Role.choices)
    department = serializers.PrimaryKeyRelatedField(queryset=Department.objects.none(), required=False, allow_null=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._scope_departments()


class StaffUpdateSerializer(_DepartmentScoped):
    first_name = serializers.CharField(max_length=150, required=False)
    last_name = serializers.CharField(max_length=150, required=False)
    role = serializers.ChoiceField(choices=User.Role.choices, required=False)
    department = serializers.PrimaryKeyRelatedField(queryset=Department.objects.none(), required=False, allow_null=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._scope_departments()

    def validate(self, attrs):
        if "email" in getattr(self, "initial_data", {}):
            raise serializers.ValidationError({"email": ["The email address cannot be changed."]})
        if not attrs:
            raise serializers.ValidationError("Nothing to update.")
        return attrs


class LinkRequestSerializer(serializers.Serializer):
    reveal_if_email_fails = serializers.BooleanField(required=False, default=False)


class DeactivateSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, max_length=staff_services.REASON_LIMIT)


class AccountEventSerializer(serializers.ModelSerializer):
    action_label = serializers.CharField(source="get_action_display", read_only=True)
    actor_name = serializers.SerializerMethodField()

    class Meta:
        model = AccountEvent
        fields = ["id", "created_at", "action", "action_label", "actor", "actor_name", "target_email", "changes", "note"]
        read_only_fields = fields

    def get_actor_name(self, obj):
        return (obj.actor.get_full_name() or obj.actor.username) if obj.actor else None
