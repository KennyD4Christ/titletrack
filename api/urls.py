from django.urls import include, path
from rest_framework.routers import SimpleRouter

from api.views.auth import CsrfView, LoginView, LogoutView, MeView
from api.views.dashboard import DashboardViewSet, MyQueueView
from api.views.imports import ImportBatchViewSet
from api.views.obligations import ObligationTypeViewSet, ObligationViewSet
from api.views.reference import (
    BuyerViewSet, DepartmentViewSet, EstateViewSet, PlotViewSet, SaleViewSet, UserViewSet,
)
from api.views.passwords import ChangePasswordView, ResetPasswordView, SetPasswordView
from api.views.sales_desk import SalesDeskViewSet
from api.views.staff import StaffViewSet

router = SimpleRouter()
router.register("estates", EstateViewSet, basename="estate")
router.register("plots", PlotViewSet, basename="plot")
router.register("buyers", BuyerViewSet, basename="buyer")
router.register("sales", SaleViewSet, basename="sale")
router.register("sales-desk", SalesDeskViewSet, basename="sales-desk")
router.register("departments", DepartmentViewSet, basename="department")
router.register("users", UserViewSet, basename="user")
router.register("staff", StaffViewSet, basename="staff")
router.register("obligation-types", ObligationTypeViewSet, basename="obligation-type")
router.register("obligations", ObligationViewSet, basename="obligation")
router.register("imports", ImportBatchViewSet, basename="import")
router.register("dashboard", DashboardViewSet, basename="dashboard")

urlpatterns = [
    path("auth/csrf/", CsrfView.as_view(), name="auth-csrf"),
    path("auth/login/", LoginView.as_view(), name="auth-login"),
    path("auth/logout/", LogoutView.as_view(), name="auth-logout"),
    path("auth/me/", MeView.as_view(), name="auth-me"),
    path("auth/set-password/", SetPasswordView.as_view(), name="auth-set-password"),
    path("auth/reset-password/", ResetPasswordView.as_view(), name="auth-reset-password"),
    path("auth/change-password/", ChangePasswordView.as_view(), name="auth-change-password"),
    path("me/queue/", MyQueueView.as_view(), name="my-queue"),
    path("", include(router.urls)),
]
