"""
Decimal-safe payroll for casual workers, driven by approved attendance.

Why not Horilla's PayrollBatch/Payslip: that engine builds payslips from
contracts and salary structures with float maths, and its monthly proration
does not express "days attended / working days entered by HR x monthly basic".
These tables hold the Demiller sheet instead; employees, departments, bank
details and LoanAccount are still Horilla's.
"""

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from employee.models import Employee

User = settings.AUTH_USER_MODEL
MONEY = {"max_digits": 12, "decimal_places": 2}


class WorkerPayProfile(models.Model):
    """Full-month basic salary for a worker from a date. Management approves."""

    PENDING, APPROVED, REJECTED = "pending", "approved", "rejected"
    STATUS = [(PENDING, _("Pending")), (APPROVED, _("Approved")), (REJECTED, _("Rejected"))]

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="pay_profiles")
    basic_salary = models.DecimalField(**MONEY, help_text=_("Full-month amount"))
    effective_from = models.DateField()
    status = models.CharField(max_length=10, choices=STATUS, default=PENDING)
    note = models.CharField(max_length=255, blank=True, default="")
    created_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="+")
    approved_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["employee_id", "-effective_from"]
        constraints = [
            models.UniqueConstraint(fields=["employee", "effective_from"], name="uniq_profile_start")
        ]
        permissions = [("approve_workerpayprofile", "Can approve worker pay structures")]

    def clean(self):
        if self.basic_salary is not None and self.basic_salary < 0:
            raise ValidationError({"basic_salary": _("Cannot be negative.")})


class FineType(models.Model):
    name = models.CharField(max_length=100, unique=True)
    default_amount = models.DecimalField(**MONEY, default=Decimal("0"))
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class Fine(models.Model):
    """HR-issued fine. Only approved fines are deducted, once, in full."""

    PENDING, APPROVED, WAIVED = "pending", "approved", "waived"
    STATUS = [(PENDING, _("Pending approval")), (APPROVED, _("Approved")), (WAIVED, _("Waived"))]

    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="payroll_fines")
    fine_type = models.ForeignKey(FineType, null=True, blank=True, on_delete=models.SET_NULL)
    description = models.CharField(max_length=255)
    amount = models.DecimalField(**MONEY)
    date_issued = models.DateField(default=timezone.localdate)
    issued_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="+")
    status = models.CharField(max_length=10, choices=STATUS, default=PENDING, db_index=True)
    approved_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    approved_at = models.DateTimeField(null=True, blank=True)
    waived_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    waived_at = models.DateTimeField(null=True, blank=True)
    waive_reason = models.CharField(max_length=255, blank=True, default="")
    applied_run = models.ForeignKey(
        "PayrollRun", null=True, blank=True, on_delete=models.PROTECT, related_name="fines_applied"
    )

    class Meta:
        ordering = ["-date_issued", "-id"]
        permissions = [
            ("approve_fine", "Can approve fines"),
            ("waive_fine", "Can waive fines"),
        ]

    def clean(self):
        if self.amount is not None and self.amount <= 0:
            raise ValidationError({"amount": _("Must be greater than zero.")})


class LoanAgreement(models.Model):
    """
    Repayment terms for a Horilla LoanAccount (which keeps the loan itself:
    employee, amount, date). Instalment k falls due on first_due_date + k-1
    weeks/months and is collected once, by the first approved run ending on or
    after that date.
    """

    WEEKLY, MONTHLY = "weekly", "monthly"
    FREQ = [(WEEKLY, _("Weekly")), (MONTHLY, _("Monthly"))]
    PENDING, APPROVED, SETTLED, CANCELLED = "pending", "approved", "settled", "cancelled"
    STATUS = [(PENDING, _("Pending approval")), (APPROVED, _("Active")), (SETTLED, _("Settled")), (CANCELLED, _("Cancelled"))]

    loan = models.OneToOneField("payroll.LoanAccount", on_delete=models.PROTECT, related_name="agreement")
    frequency = models.CharField(max_length=8, choices=FREQ, default=MONTHLY)
    instalment_amount = models.DecimalField(**MONEY)
    first_due_date = models.DateField()
    principal = models.DecimalField(**MONEY, default=Decimal("0"))
    status = models.CharField(max_length=10, choices=STATUS, default=PENDING, db_index=True)
    created_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="+")
    approved_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        permissions = [("approve_loanagreement", "Can approve loan repayment agreements")]

    def __str__(self):
        return f"{self.loan} ({self.get_frequency_display()})"

    @property
    def employee(self):
        return self.loan.employee_id

    def repaid(self):
        return self.repayments.aggregate(t=models.Sum("amount"))["t"] or Decimal("0")

    def outstanding(self):
        return self.principal - self.repaid()

    def clean(self):
        if self.instalment_amount is not None and self.instalment_amount <= 0:
            raise ValidationError({"instalment_amount": _("Must be greater than zero.")})


class PayrollRun(models.Model):
    DRAFT, REVIEWED, APPROVED = "draft", "reviewed", "approved"
    STATUS = [(DRAFT, _("Draft")), (REVIEWED, _("Reviewed")), (APPROVED, _("Approved"))]
    FINAL = (APPROVED,)

    name = models.CharField(max_length=150)
    start_date = models.DateField()
    end_date = models.DateField()
    default_working_days = models.PositiveIntegerField(
        help_text=_("Working days in the pay month, entered by HR. Can be overridden per worker.")
    )
    narration = models.CharField(max_length=150, blank=True, default="")
    department = models.ForeignKey("base.Department", null=True, blank=True, on_delete=models.SET_NULL)
    status = models.CharField(max_length=10, choices=STATUS, default=DRAFT, db_index=True)
    created_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)
    reviewed_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    approved_at = models.DateTimeField(null=True, blank=True)
    total_net = models.DecimalField(**MONEY, default=Decimal("0"))
    employee_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-end_date", "-id"]
        permissions = [
            ("review_payrollrun", "Can mark payroll reviewed"),
            ("approve_payrollrun", "Can approve payroll"),
        ]

    def __str__(self):
        return self.name

    @property
    def is_final(self):
        return self.status in self.FINAL


class PayrollLine(models.Model):
    """One worker in a run. Values are snapshots; the breakdown explains them."""

    run = models.ForeignKey(PayrollRun, on_delete=models.CASCADE, related_name="lines")
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="payroll_lines")
    emp_code = models.CharField(max_length=50, blank=True, default="")
    first_name = models.CharField(max_length=100, blank=True, default="")
    last_name = models.CharField(max_length=100, blank=True, default="")
    department = models.CharField(max_length=100, blank=True, default="")
    week_days = models.JSONField(default=list)
    total_days = models.PositiveIntegerField(default=0)
    working_days = models.PositiveIntegerField(default=0)
    working_days_override = models.PositiveIntegerField(null=True, blank=True)
    basic_salary = models.DecimalField(**MONEY, default=Decimal("0"))
    fines = models.DecimalField(**MONEY, default=Decimal("0"))
    iou = models.DecimalField(**MONEY, default=Decimal("0"))
    amount_earned = models.DecimalField(**MONEY, default=Decimal("0"))
    flags = models.JSONField(default=list, blank=True)
    breakdown = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["emp_code", "id"]
        constraints = [
            models.UniqueConstraint(fields=["run", "employee"], name="uniq_run_employee")
        ]

    def save(self, *args, **kwargs):
        if self.pk and self.run.is_final:
            raise ValidationError("An approved payroll run cannot be modified.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.run.is_final:
            raise ValidationError("An approved payroll run cannot be modified.")
        return super().delete(*args, **kwargs)


class LoanRepayment(models.Model):
    """Ledger of what has been repaid. Instalment rows are written only when a run is approved."""

    INSTALMENT, SETTLEMENT, ADJUSTMENT = "instalment", "settlement", "adjustment"
    KIND = [(INSTALMENT, _("Instalment")), (SETTLEMENT, _("Early settlement")), (ADJUSTMENT, _("Authorised adjustment"))]

    agreement = models.ForeignKey(LoanAgreement, on_delete=models.PROTECT, related_name="repayments")
    kind = models.CharField(max_length=12, choices=KIND, default=INSTALMENT)
    amount = models.DecimalField(**MONEY, help_text=_("Reduces the outstanding balance"))
    installment_no = models.PositiveIntegerField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    run = models.ForeignKey(PayrollRun, null=True, blank=True, on_delete=models.PROTECT, related_name="repayments")
    note = models.CharField(max_length=255, blank=True, default="")
    user = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["agreement", "installment_no"],
                condition=models.Q(installment_no__isnull=False),
                name="uniq_installment_once",
            )
        ]
