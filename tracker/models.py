from decimal import Decimal

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import Q
from django.utils import timezone


class Company(models.Model):
    name = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)

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


class Estate(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="estates")
    name = models.CharField(max_length=200)
    location = models.CharField(max_length=200, blank=True)
    import_batch = models.ForeignKey("ImportBatch", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["company", "name"], name="uniq_estate_per_company")]

    def __str__(self):
        return self.name


class Plot(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="plots")
    estate = models.ForeignKey(Estate, on_delete=models.CASCADE, related_name="plots")
    plot_number = models.CharField(max_length=50)
    import_batch = models.ForeignKey("ImportBatch", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["estate", "plot_number"], name="uniq_plot_per_estate")]

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

    class Meta:
        constraints = [models.UniqueConstraint(fields=["company", "name"], name="uniq_obltype_per_company")]

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

    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name="events")
    actor = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    action = models.CharField(max_length=30, choices=Action.choices)
    created_at = models.DateTimeField(auto_now_add=True)
    changes = models.JSONField(default=dict, blank=True)     # {"field": [before, after]}
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["created_at", "id"]
