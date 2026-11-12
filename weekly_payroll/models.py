"""
Decimal-safe payroll for casual workers, driven by approved attendance.

Why not Horilla's PayrollBatch/Payslip: that engine builds payslips from
contracts and salary structures with float maths, and its monthly proration
does not express "days attended / working days entered by HR x monthly basic".
These tables hold the Demiller sheet instead. Everything it needs is Horilla's own data:
the basic salary is on the employee record, days come from validated Attendance, and
loans / salary advances / fines are Horilla LoanAccount records.
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


class PayrollRun(models.Model):
    DRAFT, REVIEWED, APPROVED, EXPORTED, PAID = "draft", "reviewed", "approved", "exported", "paid"
    STATUS = [
        (DRAFT, _("Draft")), (REVIEWED, _("Reviewed")), (APPROVED, _("Approved")),
        (EXPORTED, _("Exported")), (PAID, _("Paid")),
    ]
    # Once approved the figures never change; exported/paid are later stages of the same locked run.
    FINAL = (APPROVED, EXPORTED, PAID)

    name = models.CharField(max_length=150)
    start_date = models.DateField()
    end_date = models.DateField()
    default_working_days = models.PositiveIntegerField(
        help_text=_("Working days in the pay month, entered by HR. Can be overridden per worker.")
    )
    narration = models.CharField(max_length=150, blank=True, default="")
    department = models.ForeignKey("base.Department", null=True, blank=True, on_delete=models.SET_NULL)
    employee_type = models.ForeignKey(
        "base.EmployeeType", null=True, blank=True, on_delete=models.SET_NULL, verbose_name=_("Worker category"),
    )
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


class LoanDeduction(models.Model):
    """
    One instalment of a Horilla loan, salary advance or fine that an APPROVED payroll run has taken.
    Written only when a run is approved, so drafts and recalculation never use an instalment up, and
    the database refuses to take the same instalment twice.
    """

    loan = models.ForeignKey("payroll.LoanAccount", on_delete=models.PROTECT, related_name="payroll_deductions")
    installment_no = models.PositiveIntegerField()
    due_date = models.DateField()
    amount = models.DecimalField(**MONEY)
    run = models.ForeignKey(PayrollRun, on_delete=models.PROTECT, related_name="loan_deductions")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["loan_id", "installment_no"]
        constraints = [
            models.UniqueConstraint(fields=["loan", "installment_no"], name="uniq_loan_installment_once")
        ]
