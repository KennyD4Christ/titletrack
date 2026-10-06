from rest_framework.permissions import SAFE_METHODS, BasePermission

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
    "view_home": ALL_ROLES,
    "view_my_queue": {MD, MANAGER, STAFF},
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
    """Views declare what they need. A view that declares nothing, or leaves an action unmapped, is DENIED.

    - `required_action = "name"`            one action for the whole view, or
    - `required_actions = {"list": "name"}` a map from the ViewSet action to the action it needs.
    """

    def has_permission(self, request, view):
        mapping = getattr(view, "required_actions", None)
        if mapping is not None:
            view_action = getattr(view, "action", None)
            if view_action is None:
                # The HTTP method has no handler on this viewset (e.g. DELETE on a list that is not
                # deletable). Let a logged-in company user receive the honest 405; nothing runs.
                user = request.user
                return bool(user and user.is_authenticated and user.company_id)
            action = mapping.get(view_action)
        else:
            action = getattr(view, "required_action", None)
        if action is None:
            return False
        return can(request.user, action)


class CanEditObligation(BasePermission):
    """Object-level rule on top of the role matrix.
    MD and Manager may edit any obligation; Staff only their own, or their department's."""

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        user = request.user
        if user.role in (MD, MANAGER):
            return True
        if user.role == STAFF:
            return obj.owner_id == user.id or (user.department_id is not None and obj.department_id == user.department_id)
        return False
