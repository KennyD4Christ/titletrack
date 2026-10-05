from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import (
    Buyer, Company, Department, Estate, Obligation, ObligationEvent,
    ObligationType, Plot, Sale, User,
)


class CompanyScopedAdminMixin:
    """Non-superuser staff only see (and can only pick) rows from their own company."""

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(company_id=request.user.company_id)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if not request.user.is_superuser:
            related = db_field.remote_field.model
            if related is Company:
                kwargs["queryset"] = Company.objects.filter(pk=request.user.company_id)
            elif any(f.name == "company" for f in related._meta.get_fields()):
                kwargs["queryset"] = related.objects.filter(company_id=request.user.company_id)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class ReadOnlyAdminMixin:
    """Money, ownership and history must change only through tracker/services.py."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ["name", "created_at"]


@admin.register(Department)
class DepartmentAdmin(CompanyScopedAdminMixin, admin.ModelAdmin):
    list_display = ["name", "company"]


@admin.register(Estate)
class EstateAdmin(CompanyScopedAdminMixin, admin.ModelAdmin):
    list_display = ["name", "location", "company"]
    search_fields = ["name"]


@admin.register(Plot)
class PlotAdmin(CompanyScopedAdminMixin, admin.ModelAdmin):
    list_display = ["plot_number", "estate", "company"]
    search_fields = ["plot_number"]
    list_filter = ["estate"]


@admin.register(Buyer)
class BuyerAdmin(CompanyScopedAdminMixin, admin.ModelAdmin):
    list_display = ["full_name", "phone", "company"]
    search_fields = ["full_name", "phone"]


@admin.register(ObligationType)
class ObligationTypeAdmin(CompanyScopedAdminMixin, admin.ModelAdmin):
    list_display = ["name", "trigger", "sla_days", "is_active", "company"]


@admin.register(Sale)
class SaleAdmin(ReadOnlyAdminMixin, CompanyScopedAdminMixin, admin.ModelAdmin):
    list_display = ["plot", "buyer", "price", "amount_paid", "status", "company"]
    list_filter = ["status"]


@admin.register(Obligation)
class ObligationAdmin(ReadOnlyAdminMixin, CompanyScopedAdminMixin, admin.ModelAdmin):
    list_display = ["sale", "obligation_type", "status", "owner", "due_date", "last_updated_at"]
    list_filter = ["status", "obligation_type"]


@admin.register(ObligationEvent)
class ObligationEventAdmin(ReadOnlyAdminMixin, CompanyScopedAdminMixin, admin.ModelAdmin):
    list_display = ["obligation", "actor", "created_at"]


@admin.register(User)
class UserAdmin(CompanyScopedAdminMixin, DjangoUserAdmin):
    fieldsets = DjangoUserAdmin.fieldsets + (("TitleTrack", {"fields": ("company", "department", "role")}),)
    add_fieldsets = DjangoUserAdmin.add_fieldsets + (("TitleTrack", {"fields": ("company", "department", "role")}),)
    list_display = ["username", "role", "company", "is_active"]
