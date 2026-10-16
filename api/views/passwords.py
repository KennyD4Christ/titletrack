from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from api.permissions import IsCompanyUser
from api.serializers.passwords import ChangePasswordSerializer, LinkPasswordSerializer
from tracker import account_services as acc
from tracker.models import AccountEvent, User

INVALID_LINK = "This link is invalid or has expired."


@method_decorator(csrf_protect, name="dispatch")
class _LinkPasswordView(APIView):
    """Public. The token travels in the request BODY (the link carries it in the URL fragment, which browsers
    never send to a server). Every kind of bad token gets the same answer. Rate limited per IP address
    (scope `account_link`, shared by both endpoints). Never logs anybody in."""
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "account_link"
    purpose = None
    done = ""

    def post(self, request):
        serializer = LinkPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            acc.complete_password_link(serializer.validated_data["token"], self.purpose,
                                       serializer.validated_data["password"])
        except acc.InvalidLink:
            return Response({"detail": INVALID_LINK}, status=400)
        except ValidationError as exc:
            return Response({"password": list(exc.messages)}, status=400)
        return Response({"detail": self.done})


class SetPasswordView(_LinkPasswordView):
    purpose = acc.SETUP
    done = "Your password is set. You can now sign in."


class ResetPasswordView(_LinkPasswordView):
    purpose = acc.RESET
    done = "Your password has been reset. You can now sign in."


class ChangePasswordView(APIView):
    """Signed-in users change their own password. Rate limited per user (scope `password_change`). The current
    session stays signed in; every other session of this account is signed out; outstanding links stop working."""
    permission_classes = [IsCompanyUser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password_change"

    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        current = serializer.validated_data["current_password"]
        new = serializer.validated_data["new_password"]
        user = request.user
        if not user.check_password(current):
            return Response({"current_password": ["Incorrect password."]}, status=400)
        if new == current:
            return Response({"new_password": ["The new password must be different from the current one."]}, status=400)
        try:
            validate_password(new, user)
        except ValidationError as exc:
            return Response({"new_password": list(exc.messages)}, status=400)
        with transaction.atomic():
            locked = User.objects.select_for_update().get(pk=user.pk)
            locked.set_password(new)
            locked.link_serial = F("link_serial") + 1
            locked.save(update_fields=["password", "link_serial"])
            locked.refresh_from_db()
            acc.log_account_event(locked.company_id, AccountEvent.Action.PASSWORD_CHANGED, target_user=locked, actor=locked)
        update_session_auth_hash(request, locked)
        return Response({"detail": "Your password has been changed."})
