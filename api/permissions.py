from rest_framework.permissions import BasePermission

from tracker.models import User

MD = User.Role.MD.value
MANAGER = User.Role.MANAGER.value
STAFF = User.Role.STAFF.value
SALES = User.Role.SALES.value
VIEWER = User.Role.VIEWER.value
ALL_ROLES = {MD, MANAGER, STAFF, SALES, VIEWER}

# Single source of truth for "who may do what" (mirrors Part E of the component guide).
# Object-level rules (e.g. staff may edit only their own / their department's obligations)
# are added in Week 2 on top of this role check.
MATRIX = {
    "view_reference": ALL_ROLES,
    "view_dashboard": {MD, MANAGER, VIEWER},
    "view_obligations": ALL_ROLES,
    "view_history": {MD, MANAGER, STAFF, VIEWER},
    "edit_obligation": {MD, MANAGER, STAFF},
    "bulk_update": {MD, MANAGER},
    "import_data": {MD, MANAGER},
    "manage_settings": {MD, MANAGER},
    "manage_sales": {MD, MANAGER},
    "upload_attachment": {MD, MANAGER, STAFF},
    "download_attachment": {MD, MANAGER, STAFF, VIEWER},
    "buyer_message": {MD, MANAGER, STAFF, SALES},
    "export": {MD, MANAGER, VIEWER},
}


def can(user, action):
    """True only for an authenticated user who belongs to a company and whose role allows the action."""
    if not (user and user.is_authenticated and user.company_id):
        return False
    return user.role in MATRIX[action]


class IsCompanyUser(BasePermission):
    """Any authenticated user attached to a company."""

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.company_id)


class ActionPermission(BasePermission):
    """Views declare `required_action`. A view that forgets to declare one is DENIED (fail closed)."""

    def has_permission(self, request, view):
        action = getattr(view, "required_action", None)
        if action is None:
            return False
        return can(request.user, action)
