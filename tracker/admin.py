from django import forms
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse

from . import onboarding_services as onboarding
from .models import (
    AccountEvent, Buyer, Company, Department, Estate, Obligation, ObligationEvent,
    ObligationType, Payment, Plot, Sale, SaleEvent, User,
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


class OnboardCompanyForm(forms.Form):
    """Collects the input only. Every rule (names, duplicates, email) is enforced by the onboarding service, so the
    command and this page cannot drift apart; that is why the text fields are not marked required here."""

    company = forms.CharField(label="Company name", required=False)
    departments = forms.CharField(label="Departments", required=False, widget=forms.Textarea(attrs={"rows": 5}),
                                  help_text="One per line. Optional.")
    md_email = forms.CharField(label="MD email address", required=False, widget=forms.EmailInput)
    md_first_name = forms.CharField(label="MD first name", required=False)
    md_last_name = forms.CharField(label="MD last name", required=False)
    reveal_if_email_fails = forms.BooleanField(
        required=False, label="If the email cannot be sent, show me the setup link once",
        help_text="Leave this off to keep the link out of the browser. You can issue a new link later.")

    def service_kwargs(self):
        data = self.cleaned_data
        return dict(company=data["company"], departments=[d for d in data["departments"].splitlines() if d.strip()],
                    md_email=data["md_email"], md_first_name=data["md_first_name"],
                    md_last_name=data["md_last_name"], reveal_if_email_fails=data["reveal_if_email_fails"])


class ResendSetupLinkForm(forms.Form):
    reveal_if_email_fails = forms.BooleanField(
        required=False, label="If the email cannot be sent, show me the setup link once",
        help_text="Leave this off to keep the link out of the browser.")


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    """Company onboarding for platform operators (users given tracker.onboard_company directly).
    Companies are created only through the onboarding page. The standard add form is disabled for everyone,
    superusers included, because it would bypass the onboarding rules."""

    list_display = ["name", "created_at"]
    actions = ["resend_setup_link"]
    onboard_template = "admin/tracker/company/onboard.html"

    # --- who may do it ---

    def has_add_permission(self, request):
        return False

    def add_view(self, request, form_url="", extra_context=None):
        """The standard add form is never shown. A designated operator who reaches this URL is sent to the
        onboarding page; everyone else is refused. (Signed-out users never get this far: admin_view sends
        them to the login page.)"""
        if self.has_onboard_permission(request) and "_popup" not in request.GET:
            return HttpResponseRedirect(
                reverse("admin:%s_%s_onboard" % (self.opts.app_label, self.opts.model_name)))
        raise PermissionDenied

    def has_onboard_permission(self, request):
        return onboarding.is_platform_operator(request.user)

    def get_urls(self):
        info = self.opts.app_label, self.opts.model_name
        wrap = self.admin_site.admin_view        # signed-in staff only; the operator check follows in each view
        return [
            path("onboard/", wrap(self.onboard_view), name="%s_%s_onboard" % info),
            path("<int:company_id>/resend-setup-link/", wrap(self.resend_view),
                 name="%s_%s_resend_setup_link" % info),
        ] + super().get_urls()

    # --- pages ---

    def _page(self, request, title, **context):
        context = {**self.admin_site.each_context(request), "opts": self.opts, "title": title, **context}
        response = TemplateResponse(request, self.onboard_template, context)
        response["Cache-Control"] = "no-store"          # these pages can carry a one-time credential
        response["Pragma"] = "no-cache"
        return response

    def onboard_view(self, request):
        if not self.has_onboard_permission(request):
            raise PermissionDenied
        title = "Onboard a company"
        if request.method != "POST":
            return self._page(request, title, stage="form", form=OnboardCompanyForm())
        form = OnboardCompanyForm(request.POST)
        if form.is_valid():
            try:
                result, outcome = onboarding.onboard_as_operator(request.user, **form.service_kwargs())
            except onboarding.OnboardingError as exc:
                form.add_error(exc.field if exc.field in form.fields else None, exc.message)
            else:
                self.log_addition(request, result.company,
                                  f"Onboarded through platform onboarding (setup link: {outcome.status}).")
                return self._page(
                    request, "Company onboarded", stage="created", company=result.company, md=result.md,
                    departments=result.departments, outcome=outcome,
                    resend_url=reverse("admin:%s_%s_resend_setup_link" % (self.opts.app_label, self.opts.model_name),
                                       args=[result.company.pk]))
        return self._page(request, title, stage="form", form=form)

    def resend_view(self, request, company_id):
        if not self.has_onboard_permission(request):
            raise PermissionDenied
        company = self.get_queryset(request).filter(pk=company_id).first()
        if company is None:
            raise Http404
        title = "Issue a new setup link"
        if request.method != "POST":
            try:
                md = onboarding.waiting_md(company)
            except onboarding.ResendError as exc:
                return self._page(request, title, stage="refused", company=company, error=str(exc))
            return self._page(request, title, stage="confirm", company=company, md=md, form=ResendSetupLinkForm())
        form = ResendSetupLinkForm(request.POST)
        reveal = form.is_valid() and form.cleaned_data["reveal_if_email_fails"]
        try:
            md, outcome = onboarding.reissue_md_setup_link(request.user, company.pk, reveal_if_email_fails=reveal)
        except onboarding.ResendError as exc:
            return self._page(request, title, stage="refused", company=company, error=str(exc))
        self.log_change(request, company, f"Issued a new setup link for the MD (setup link: {outcome.status}).")
        return self._page(
            request, "Setup link issued", stage="reissued", company=company, md=md, outcome=outcome,
            resend_url=request.path)

    @admin.action(description="Issue a new setup link for the company's MD", permissions=["onboard"])
    def resend_setup_link(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, "Select exactly one company.", messages.ERROR)
            return None
        return HttpResponseRedirect(reverse(
            "admin:%s_%s_resend_setup_link" % (self.opts.app_label, self.opts.model_name), args=[queryset.get().pk]))


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


@admin.register(Payment)
class PaymentAdmin(ReadOnlyAdminMixin, CompanyScopedAdminMixin, admin.ModelAdmin):
    list_display = ["sale", "kind", "amount", "paid_on", "recorded_by", "created_at"]
    list_filter = ["kind"]


@admin.register(SaleEvent)
class SaleEventAdmin(ReadOnlyAdminMixin, CompanyScopedAdminMixin, admin.ModelAdmin):
    list_display = ["sale", "action", "actor", "created_at"]
    list_filter = ["action"]


@admin.register(Obligation)
class ObligationAdmin(ReadOnlyAdminMixin, CompanyScopedAdminMixin, admin.ModelAdmin):
    list_display = ["sale", "obligation_type", "status", "owner", "due_date", "last_updated_at"]
    list_filter = ["status", "obligation_type"]


@admin.register(ObligationEvent)
class ObligationEventAdmin(ReadOnlyAdminMixin, CompanyScopedAdminMixin, admin.ModelAdmin):
    list_display = ["obligation", "actor", "created_at"]


@admin.register(AccountEvent)
class AccountEventAdmin(ReadOnlyAdminMixin, CompanyScopedAdminMixin, admin.ModelAdmin):
    list_display = ["target_email", "action", "actor", "created_at"]
    list_filter = ["action"]


@admin.register(User)
class UserAdmin(CompanyScopedAdminMixin, DjangoUserAdmin):
    fieldsets = DjangoUserAdmin.fieldsets + (("TitleTrack", {"fields": ("company", "department", "role")}),)
    add_fieldsets = DjangoUserAdmin.add_fieldsets + (("TitleTrack", {"fields": ("company", "department", "role")}),)
    list_display = ["username", "role", "company", "is_active"]
