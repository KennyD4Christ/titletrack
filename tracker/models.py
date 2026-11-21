import contextvars
from decimal import Decimal

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower
from django.utils import timezone

from .canonical import MAX_CASEFOLD_GROWTH, canonical_key, fields_with_derived_key, sync_key_on_save


class Company(models.Model):
    name = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        permissions = [("onboard_company", "Can onboard a new company")]

    def __str__(self):
        return self.name


class Department(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="departments")
    name = models.CharField(max_length=100)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["company", "name"], name="uniq_department_per_company")
        ]

    def __str__(self):
        return self.name


class User(AbstractUser):
    class Role(models.TextChoices):
        MD = "md", "MD / Director"
        MANAGER = "manager", "Documentation / Operations manager"
        STAFF = "staff", "Staff"
        SALES = "sales", "Sales"
        VIEWER = "viewer", "Viewer"

    company = models.ForeignKey(Company, null=True, blank=True, on_delete=models.CASCADE, related_name="users")
    department = models.ForeignKey(Department, null=True, blank=True, on_delete=models.SET_NULL, related_name="members")
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.STAFF)
    # Bumped whenever a password-setup/reset link is issued, a password is set, or the account is
    # deactivated. It is part of every link's signed contents, so a bump kills all outstanding links.
    link_serial = models.PositiveIntegerField(default=0)

    class Meta(AbstractUser.Meta):
        constraints = [
            models.UniqueConstraint(Lower("email"), condition=~Q(email=""), name="uniq_user_email_ci"),
        ]


# True only while CanonicalKeyQuerySet.bulk_update is running. Django's own bulk_update writes through
# QuerySet.update() with one CASE expression per column, including the key column; our bulk_update has already
# derived those keys from their sources, so update() lets that one internal call through. A context variable
# (not an attribute on the queryset) because Django runs that call on a clone of the queryset.
_bulk_update_in_progress = contextvars.ContextVar("canonical_key_bulk_update", default=False)


class CanonicalKeyQuerySet(models.QuerySet):
    """Keeps a stored canonical key in step with the field it is derived from, on every write path that does not
    go through Model.save() (which does the same for itself): bulk_create, bulk_update and update().

    A subclass names `source_field` and `key_field`. The key is DERIVED: it is always computed here from the
    source, and any attempt to write it independently is refused, so a stale or hand-written key cannot get in.

    Deliberately NOT covered, because they bypass the ORM's queryset methods altogether: raw SQL, QuerySet._update
    and Model.save_base. The database still refuses a blank key and a duplicate key, so the worst such a path can
    do is be refused. Migrations and maintenance code that must write a key use Model._base_manager."""

    source_field = None
    key_field = None

    def _source_value(self, value):
        if hasattr(value, "resolve_expression"):
            # An expression (F(), Concat() and so on) cannot be reduced to a key in Python.
            raise TypeError(
                f"{self.model.__name__}.{self.source_field} must be written as a plain value, not an expression, "
                f"so that {self.key_field} can be kept in step.")
        return value

    def bulk_create(self, objs, *args, **kwargs):
        objs = list(objs)
        for obj in objs:
            setattr(obj, self.key_field, canonical_key(self._source_value(getattr(obj, self.source_field))))
        if kwargs.get("update_fields") is not None:        # update_conflicts: the columns rewritten on a conflict
            kwargs["update_fields"] = fields_with_derived_key(kwargs["update_fields"], self.source_field, self.key_field)
        return super().bulk_create(objs, *args, **kwargs)

    def bulk_update(self, objs, fields, *args, **kwargs):
        fields = fields_with_derived_key(fields, self.source_field, self.key_field)   # refuses the key on its own
        if self.source_field in fields:
            objs = list(objs)
            for obj in objs:
                setattr(obj, self.key_field, canonical_key(self._source_value(getattr(obj, self.source_field))))
        token = _bulk_update_in_progress.set(True)
        try:
            return super().bulk_update(objs, fields, *args, **kwargs)
        finally:
            _bulk_update_in_progress.reset(token)

    def update(self, **kwargs):
        if not _bulk_update_in_progress.get():
            if self.key_field in kwargs:
                raise ValueError(
                    f"{self.model.__name__}.{self.key_field} is derived from {self.source_field} and cannot be "
                    f"written directly: update {self.source_field} instead, and {self.key_field} will follow it.")
            if self.source_field in kwargs:
                kwargs[self.key_field] = canonical_key(self._source_value(kwargs[self.source_field]))
        return super().update(**kwargs)


class EstateQuerySet(CanonicalKeyQuerySet):
    source_field = "name"
    key_field = "name_key"


class PlotQuerySet(CanonicalKeyQuerySet):
    source_field = "plot_number"
    key_field = "plot_key"


class Estate(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="estates")
    name = models.CharField(max_length=200)
    location = models.CharField(max_length=200, blank=True)
    import_batch = models.ForeignKey("ImportBatch", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    # The canonical form of `name` (see tracker/canonical.py). Maintained by save() and the queryset above;
    # never set by hand. The database enforces it as unique per company, so "Palm Grove" and "palm  grove"
    # cannot both exist, whichever code path created them. db_default keeps older code able to insert while a
    # deployment is part-way through (migration 0014 then refuses a blank key, so a row that skipped save() is
    # caught rather than stored).
    name_key = models.CharField(
        max_length=200 * MAX_CASEFOLD_GROWTH, default="", db_default="", blank=True, editable=False)

    objects = EstateQuerySet.as_manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["company", "name"], name="uniq_estate_per_company"),
            models.UniqueConstraint(fields=["company", "name_key"], name="uniq_estate_key_per_company"),
            models.CheckConstraint(condition=~Q(name_key=""), name="estate_key_not_blank"),
        ]

    def save(self, *args, **kwargs):
        kwargs = sync_key_on_save(self, "name", "name_key", args, kwargs)
        super().save(**kwargs)

    def __str__(self):
        return self.name


class Plot(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="plots")
    estate = models.ForeignKey(Estate, on_delete=models.CASCADE, related_name="plots")
    plot_number = models.CharField(max_length=50)
    import_batch = models.ForeignKey("ImportBatch", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    # The canonical form of `plot_number`; see Estate.name_key. Unique per estate.
    plot_key = models.CharField(
        max_length=50 * MAX_CASEFOLD_GROWTH, default="", db_default="", blank=True, editable=False)

    objects = PlotQuerySet.as_manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["estate", "plot_number"], name="uniq_plot_per_estate"),
            models.UniqueConstraint(fields=["estate", "plot_key"], name="uniq_plot_key_per_estate"),
            models.CheckConstraint(condition=~Q(plot_key=""), name="plot_key_not_blank"),
        ]

    def save(self, *args, **kwargs):
        kwargs = sync_key_on_save(self, "plot_number", "plot_key", args, kwargs)
        super().save(**kwargs)

    def __str__(self):
        return f"{self.estate.name} / {self.plot_number}"


class Buyer(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="buyers")
    full_name = models.CharField(max_length=200)
    phone = models.CharField(max_length=40, blank=True)
    email = models.EmailField(blank=True)
    import_batch = models.ForeignKey("ImportBatch", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    def __str__(self):
        return self.full_name


class Sale(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        CANCELLED = "cancelled", "Cancelled"

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="sales")
    plot = models.ForeignKey(Plot, on_delete=models.PROTECT, related_name="sales")
    buyer = models.ForeignKey(Buyer, on_delete=models.PROTECT, related_name="sales")
    price = models.DecimalField(max_digits=14, decimal_places=2)
    amount_paid = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    sale_date = models.DateField()
    fully_paid_on = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    import_batch = models.ForeignKey("ImportBatch", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    cancel_reason = models.TextField(blank=True)
    # True only for a sale created with an explicit document selection. Such a sale gets exactly the documents
    # chosen for it and never the automatic ones. Every sale that already exists stays False (legacy behaviour).
    documents_managed = models.BooleanField(default=False, db_default=False)

    class Meta:
        constraints = [
            # A plot can have only one ACTIVE sale. Cancelled sales do not block a resale.
            models.UniqueConstraint(
                fields=["plot"], condition=Q(status="active"), name="one_active_sale_per_plot"
            ),
            models.CheckConstraint(condition=Q(amount_paid__gte=0), name="sale_amount_paid_non_negative"),
        ]

    @property
    def is_fully_paid(self):
        return self.amount_paid >= self.price


class ObligationType(models.Model):
    class Trigger(models.TextChoices):
        ON_SALE = "on_sale", "When the sale is created"
        ON_FULL_PAYMENT = "on_full_payment", "When the buyer is fully paid"

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="obligation_types")
    name = models.CharField(max_length=120)
    trigger = models.CharField(max_length=20, choices=Trigger.choices, default=Trigger.ON_FULL_PAYMENT)
    sla_days = models.PositiveIntegerField(default=90)
    default_department = models.ForeignKey(Department, null=True, blank=True, on_delete=models.SET_NULL)
    is_active = models.BooleanField(default=True)
    # False for catalogue-only types: they are never created automatically. Every existing type stays True.
    auto_generate = models.BooleanField(default=True, db_default=True)
    # The catalogue entry this type is the work-tracking twin of. Multiple obligation types may share one entry.
    catalogue_entry = models.ForeignKey(
        "DocumentType", null=True, blank=True, on_delete=models.PROTECT, related_name="obligation_types")
    # Exactly one work type per catalogue entry is current: the one used for new documents. The others are
    # earlier work types, kept so obligations created under an earlier name keep it. Set only by the catalogue
    # services (tracker/document_services.py).
    is_current = models.BooleanField(default=False, db_default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["company", "name"], name="uniq_obltype_per_company"),
            # At most one current work type per catalogue entry. Rows without an entry are not constrained:
            # NULLs never collide in a unique index.
            models.UniqueConstraint(
                fields=["catalogue_entry"], condition=Q(is_current=True), name="one_current_worktype_per_entry"),
            # ...and a current work type must belong to an entry.
            models.CheckConstraint(
                condition=Q(is_current=False) | Q(catalogue_entry__isnull=False), name="current_worktype_has_entry"),
        ]

    def __str__(self):
        return self.name


class DocumentType(models.Model):
    """One kind of document in a company's catalogue: what a sale can include. The app never deletes a row; a
    type that has been used is deactivated instead. Its work-tracking twin is the ObligationType that points
    at it through `ObligationType.catalogue_entry` (reverse name: `obligation_type`)."""

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="document_types")
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    category = models.CharField(max_length=60, blank=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint("company", Lower("name"), name="uniq_doctype_name_ci_per_company"),
        ]

    def __str__(self):
        return self.name


class Obligation(models.Model):
    class Status(models.TextChoices):
        NOT_STARTED = "not_started", "Not started"
        IN_PROGRESS = "in_progress", "In progress"
        AWAITING_EXTERNAL = "awaiting_external", "Awaiting external party"
        COMPLETED = "completed", "Completed"
        NOT_APPLICABLE = "not_applicable", "Not applicable"

    OPEN_STATUSES = (Status.NOT_STARTED, Status.IN_PROGRESS, Status.AWAITING_EXTERNAL)

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="obligations")
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name="obligations")
    obligation_type = models.ForeignKey(ObligationType, on_delete=models.PROTECT, related_name="obligations")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NOT_STARTED)
    stage = models.CharField(max_length=120, blank=True)
    owner = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="owned_obligations")
    department = models.ForeignKey(Department, null=True, blank=True, on_delete=models.SET_NULL)
    external_party = models.CharField(max_length=200, blank=True)
    reason = models.TextField(blank=True)
    next_action = models.TextField(blank=True)
    next_review_date = models.DateField(null=True, blank=True)
    started_on = models.DateField()
    due_date = models.DateField(null=True, blank=True)
    completed_on = models.DateField(null=True, blank=True)
    # Last HUMAN update. System saves must not touch this, or stale flags become meaningless.
    last_updated_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["sale", "obligation_type"], name="one_obligation_per_sale_and_type")
        ]
        indexes = [models.Index(fields=["company", "status"]), models.Index(fields=["company", "due_date"])]


class ObligationEvent(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    obligation = models.ForeignKey(Obligation, on_delete=models.CASCADE, related_name="events")
    actor = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    changes = models.JSONField(default=dict, blank=True)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["created_at", "id"]


class ImportBatch(models.Model):
    """One spreadsheet import: the parsed file, how columns were mapped, the dry-run report and the outcome."""

    class Status(models.TextChoices):
        UPLOADED = "uploaded", "Uploaded"
        MAPPED = "mapped", "Mapped"
        VALIDATED = "validated", "Dry run done"
        COMMITTED = "committed", "Imported"
        UNDONE = "undone", "Undone"

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="import_batches")
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    filename = models.CharField(max_length=255)
    sheet_name = models.CharField(max_length=200, blank=True)
    sheets = models.JSONField(default=list, blank=True)
    file_sha256 = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.UPLOADED)
    headers = models.JSONField(default=list)
    rows = models.JSONField(default=list)            # [{"n": sheet row number, "v": [cell values]}]
    row_count = models.PositiveIntegerField(default=0)
    mapping = models.JSONField(default=dict, blank=True)
    status_mapping = models.JSONField(default=dict, blank=True)
    report = models.JSONField(default=dict, blank=True)
    counts = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    committed_at = models.DateTimeField(null=True, blank=True)
    undone_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]


class Payment(models.Model):
    """Append-only payment ledger. Rows are never edited or deleted by the app: a mistake is fixed with a
    reversal entry. `Sale.amount_paid` is the stored total, updated only by tracker/services.py in the same
    transaction as the ledger row."""

    class Kind(models.TextChoices):
        PAYMENT = "payment", "Payment"
        REVERSAL = "reversal", "Reversal"
        OPENING = "opening", "Opening balance"
        ADJUSTMENT = "adjustment", "Adjustment"

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="payments")
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name="payments")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    amount = models.DecimalField(max_digits=14, decimal_places=2)      # negative for reversals
    paid_on = models.DateField()
    reference = models.CharField(max_length=120, blank=True)
    note = models.TextField(blank=True)                                # a reversal's note is its reason
    reverses = models.OneToOneField(
        "self", null=True, blank=True, on_delete=models.RESTRICT, related_name="reversed_by")
    recorded_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    idempotency_key = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["sale", "idempotency_key"], condition=~Q(idempotency_key=""),
                name="uniq_payment_key_per_sale"),
            models.CheckConstraint(condition=~Q(amount=0), name="payment_amount_non_zero"),
            models.CheckConstraint(
                condition=(
                    Q(kind__in=["payment", "opening"], amount__gt=0)
                    | Q(kind="reversal", amount__lt=0)
                    | Q(kind="adjustment")
                ),
                name="payment_amount_sign_matches_kind"),
        ]


class SaleEvent(models.Model):
    """Sale-level audit history: who did what, when, with before/after values."""

    class Action(models.TextChoices):
        CREATED = "created", "Sale created"
        PAYMENT = "payment", "Payment recorded"
        PAYMENT_REVERSED = "payment_reversed", "Payment reversed"
        BUYER_EDITED = "buyer_edited", "Buyer details edited"
        CANCELLED = "cancelled", "Sale cancelled"
        REINSTATED = "reinstated", "Sale reinstated"
        DOCUMENT_ADDED = "document_added", "Document added"
        DOCUMENT_ISSUED = "document_issued", "Document issued"
        DOCUMENT_ISSUANCE_CORRECTED = "document_issuance_corrected", "Document issuance corrected"
        DOCUMENT_NOT_APPLICABLE = "document_not_applicable", "Document marked not applicable"

    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name="events")
    actor = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    action = models.CharField(max_length=30, choices=Action.choices)
    created_at = models.DateTimeField(auto_now_add=True)
    changes = models.JSONField(default=dict, blank=True)     # {"field": [before, after]}
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["created_at", "id"]


class DocumentTypeEvent(models.Model):
    """Catalogue history: who did what to which document type, when, with before/after values.
    Rows are append-only: the app never edits or deletes them."""

    class Action(models.TextChoices):
        CREATED = "created", "Document type created"
        EDITED = "edited", "Document type edited"
        RENAMED = "renamed", "Document type renamed"
        ACTIVATED = "activated", "Document type activated"
        DEACTIVATED = "deactivated", "Document type deactivated"

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="document_type_events")
    document_type = models.ForeignKey(DocumentType, on_delete=models.PROTECT, related_name="events")
    actor = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    action = models.CharField(max_length=30, choices=Action.choices)
    created_at = models.DateTimeField(auto_now_add=True)
    changes = models.JSONField(default=dict, blank=True)     # {"field": [before, after]}
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [models.Index(fields=["company", "document_type"], name="doctypeevent_company_type_idx")]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("Document type history cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Document type history cannot be deleted.")


class SaleDocument(models.Model):
    """One document on one sale: owed to the buyer, already issued, or not required. Never deleted by the app.
    An owed document is backed by an Obligation (the work of preparing it); the link is kept after issuance.
    `name_snapshot` is the catalogue name when the document was added, so later renames never change history."""

    class State(models.TextChoices):
        OWED = "owed", "Owed"
        ISSUED = "issued", "Issued"
        NOT_APPLICABLE = "not_applicable", "Not applicable"

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="sale_documents")
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name="documents")
    document_type = models.ForeignKey(DocumentType, on_delete=models.PROTECT, related_name="sale_documents")
    name_snapshot = models.CharField(max_length=120)
    state = models.CharField(max_length=20, choices=State.choices, default=State.OWED)
    obligation = models.OneToOneField(
        Obligation, null=True, blank=True, on_delete=models.PROTECT, related_name="sale_document")
    issued_on = models.DateField(null=True, blank=True)
    reference = models.CharField(max_length=120, blank=True)
    issued_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    issued_at = models.DateTimeField(null=True, blank=True)
    not_applicable_reason = models.TextField(blank=True)
    not_applicable_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    not_applicable_at = models.DateTimeField(null=True, blank=True)
    added_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["sale", "document_type"], name="one_document_per_sale_and_type"),
            models.CheckConstraint(
                condition=~Q(state="owed") | Q(obligation__isnull=False), name="saledoc_owed_has_obligation"),
            models.CheckConstraint(
                condition=~Q(state="issued") | Q(issued_on__isnull=False), name="saledoc_issued_has_date"),
            models.CheckConstraint(
                condition=~Q(state="not_applicable") | ~Q(not_applicable_reason=""), name="saledoc_na_has_reason"),
        ]
        # `sale` needs no explicit index: the foreign key already has one.
        indexes = [models.Index(fields=["company", "state"], name="saledoc_company_state_idx")]


class AccountEvent(models.Model):
    """Account-level audit history (who did what to which account, when, with before/after values).
    Never holds passwords, tokens or links. Rows are append-only: the app never edits or deletes them."""

    class Action(models.TextChoices):
        CREATED = "created", "Account created"
        PROFILE_UPDATED = "profile_updated", "Profile updated"
        ROLE_CHANGED = "role_changed", "Role changed"
        DEACTIVATED = "deactivated", "Account deactivated"
        REACTIVATED = "reactivated", "Account reactivated"
        SETUP_LINK_ISSUED = "setup_link_issued", "Setup link issued"
        RESET_LINK_ISSUED = "reset_link_issued", "Reset link issued"
        PASSWORD_SET = "password_set", "Password set through a link"
        PASSWORD_CHANGED = "password_changed", "Password changed"
        EMAIL_FAILED = "email_failed", "Link email not delivered"
        LINK_REVEALED = "link_revealed", "Link shown to an administrator"

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="account_events")
    target_user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="account_events")
    target_email = models.CharField(max_length=254, blank=True)   # the address at the time of the event
    actor = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    action = models.CharField(max_length=30, choices=Action.choices)
    created_at = models.DateTimeField(auto_now_add=True)
    changes = models.JSONField(default=dict, blank=True)     # {"field": [before, after]}
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [models.Index(fields=["company", "target_user"])]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("Account history cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Account history cannot be deleted.")
