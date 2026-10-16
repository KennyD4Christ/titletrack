from django.contrib.auth import authenticate, login, logout
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api.auth_utils import authenticate_identifier
from api.permissions import IsCompanyUser
from api.serializers.auth import LoginSerializer, MeSerializer


@method_decorator(ensure_csrf_cookie, name="dispatch")
class CsrfView(APIView):
    """Call once on app start so the browser holds a CSRF cookie."""
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"detail": "CSRF cookie set."})


@method_decorator(csrf_protect, name="dispatch")
class LoginView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = authenticate_identifier(
            request,
            serializer.validated_data["username"],
            serializer.validated_data["password"],
        )
        # One generic message for every failure: wrong password, inactive, or no company.
        if user is None or not user.is_active or user.company_id is None:
            return Response({"detail": "Invalid credentials."}, status=400)
        login(request, user)
        return Response(MeSerializer(user).data)


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        logout(request)
        return Response(status=204)


class MeView(APIView):
    permission_classes = [IsCompanyUser]

    def get(self, request):
        return Response(MeSerializer(request.user).data)
