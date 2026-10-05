from django.urls import include, path
from rest_framework.routers import SimpleRouter

from api.views.auth import CsrfView, LoginView, LogoutView, MeView
from api.views.reference import (
    BuyerViewSet, DepartmentViewSet, EstateViewSet, PlotViewSet, SaleViewSet, UserViewSet,
)

router = SimpleRouter()
router.register("estates", EstateViewSet, basename="estate")
router.register("plots", PlotViewSet, basename="plot")
router.register("buyers", BuyerViewSet, basename="buyer")
router.register("sales", SaleViewSet, basename="sale")
router.register("departments", DepartmentViewSet, basename="department")
router.register("users", UserViewSet, basename="user")

urlpatterns = [
    path("auth/csrf/", CsrfView.as_view(), name="auth-csrf"),
    path("auth/login/", LoginView.as_view(), name="auth-login"),
    path("auth/logout/", LogoutView.as_view(), name="auth-logout"),
    path("auth/me/", MeView.as_view(), name="auth-me"),
    path("", include(router.urls)),
]
