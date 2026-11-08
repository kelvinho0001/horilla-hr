"""Bank directory, per-worker bank selection and the history of payment-file exports."""

from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

User = settings.AUTH_USER_MODEL


class Bank(models.Model):
    """A bank and its code. Codes are text ('044' must keep its zero; '035A' exists)."""

    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=150)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.code})"


class EmployeeBankLink(models.Model):
    """
    Adds the bank drop-down to Horilla's EmployeeBankDetails without editing it:
    the account number stays on the Horilla record; this says which listed bank
    it belongs to (the code then comes from the directory, never typed).
    """

    details = models.OneToOneField(
        "employee.EmployeeBankDetails", on_delete=models.CASCADE, related_name="bank_link"
    )
    bank = models.ForeignKey(Bank, on_delete=models.PROTECT, related_name="links")

    def __str__(self):
        return f"{self.details} -> {self.bank}"


DEFAULT_COLUMNS = [
    {"field": "emp_id", "header": "EMP ID"},
    {"field": "first_name", "header": "FIRST NAME"},
    {"field": "last_name", "header": "LAST NAME"},
    {"field": "department", "header": "DEPARTMENT"},
    {"field": "account_number", "header": "ACCOUNT NUMBER"},
    {"field": "beneficiary_name", "header": "BENEFICIARY NAME"},
    {"field": "amount", "header": "AMOUNT"},
    {"field": "bank_code", "header": "BANK CODE"},
    {"field": "narration", "header": "NARRATION"},
]
FIELD_CHOICES = [
    "emp_id", "first_name", "last_name", "department", "account_number", "beneficiary_name",
    "amount", "bank_code", "bank_name", "narration", "reference",
]


class PaymentTemplate(models.Model):
    """Column names and order of the PAYMENT SCHEDULE sheet. The default mirrors Demiller's sheet."""

    name = models.CharField(max_length=100, unique=True)
    is_default = models.BooleanField(default=False)
    columns = models.JSONField(
        default=list,
        help_text=_('List of {"field": ..., "header": ...}. Fields: ' + ", ".join(FIELD_CHOICES)),
    )

    def __str__(self):
        return self.name


class ExportBatch(models.Model):
    GENERATED, SUPERSEDED, PAID = "generated", "superseded", "paid"
    STATUS = [(GENERATED, _("Exported")), (SUPERSEDED, _("Superseded")), (PAID, _("Paid"))]

    run = models.ForeignKey("weekly_payroll.PayrollRun", on_delete=models.PROTECT, related_name="exports")
    reference = models.CharField(max_length=40, unique=True)
    status = models.CharField(max_length=10, choices=STATUS, default=GENERATED, db_index=True)
    narration = models.CharField(max_length=150)
    employee_count = models.PositiveIntegerField()
    total_amount = models.DecimalField(max_digits=14, decimal_places=2)
    excluded = models.JSONField(default=list, blank=True)
    template_name = models.CharField(max_length=100, blank=True, default="")
    # Kept in the database (not /media) so bank details are only reachable through the permission-checked view.
    content = models.BinaryField()
    sha256 = models.CharField(max_length=64)
    created_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)
    superseded_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    superseded_at = models.DateTimeField(null=True, blank=True)
    supersede_reason = models.CharField(max_length=255, blank=True, default="")
    paid_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    paid_at = models.DateTimeField(null=True, blank=True)
    download_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            # At most one live export per payroll run: the database itself refuses a second payment file.
            models.UniqueConstraint(
                fields=["run"], condition=models.Q(status__in=["generated", "paid"]),
                name="uniq_live_export_per_run",
            )
        ]
        permissions = [
            ("generate_export", "Can generate bank payment files"),
            ("supersede_export", "Can supersede a bank payment file"),
            ("mark_export_paid", "Can mark an export as paid"),
            ("view_bank_accounts", "Can view worker bank accounts"),
            ("edit_bank_accounts", "Can edit worker bank accounts"),
        ]

    def __str__(self):
        return self.reference
