"""
Daily attendance derived from raw biometric transactions.

One DailyAttendance row per employee per day. Raw punches stay in
biometric_integration.RawTransaction; this table is what HR reviews, corrects
and approves, and what payroll reads (approved rows only).
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from employee.models import Employee
from horilla.models import HorillaModel

PRESENT, MISSING, REVIEW, ABSENT, LEAVE, HOLIDAY, REST = (
    "present", "missing", "review", "absent", "leave", "holiday", "rest",
)
STATUS_CHOICES = [
    (PRESENT, _("Present")),
    (MISSING, _("Missing - needs review")),
    (REVIEW, _("Punch outside window - needs review")),
    (ABSENT, _("Absent")),
    (LEAVE, _("Approved leave")),
    (HOLIDAY, _("Holiday")),
    (REST, _("Rest day")),
]
UNRESOLVED = (MISSING, REVIEW)
# What HR may set by hand (never the system "needs review" states).
CORRECTABLE = (PRESENT, ABSENT, LEAVE, HOLIDAY, REST)

WEEKDAYS = [
    (0, _("Monday")), (1, _("Tuesday")), (2, _("Wednesday")), (3, _("Thursday")),
    (4, _("Friday")), (5, _("Saturday")), (6, _("Sunday")),
]


class AttendanceRule(HorillaModel):
    """
    When a punch counts as attendance, and which weekdays are working days.
    The most specific active rule wins: employee > worker category >
    department > shift > global. Nothing is hard-coded: with no rule, any punch
    on a Monday-Saturday counts (see services.FALLBACK_WEEKDAYS).
    """

    GLOBAL, SHIFT, DEPARTMENT, CATEGORY, EMPLOYEE = (
        "global", "shift", "department", "category", "employee",
    )
    SCOPE_CHOICES = [
        (GLOBAL, _("Everyone")),
        (SHIFT, _("Shift")),
        (DEPARTMENT, _("Department")),
        (CATEGORY, _("Worker category (employee type)")),
        (EMPLOYEE, _("Individual employee")),
    ]
    name = models.CharField(max_length=100)
    scope = models.CharField(max_length=12, choices=SCOPE_CHOICES, default=GLOBAL)
    department = models.ForeignKey(
        "base.Department", null=True, blank=True, on_delete=models.CASCADE
    )
    shift = models.ForeignKey(
        "base.EmployeeShift", null=True, blank=True, on_delete=models.CASCADE
    )
    employee_type = models.ForeignKey(
        "base.EmployeeType", null=True, blank=True, on_delete=models.CASCADE
    )
    employee = models.ForeignKey(
        Employee, null=True, blank=True, on_delete=models.CASCADE,
        related_name="attendance_rules",
    )
    window_start = models.TimeField(
        null=True, blank=True,
        help_text=_("Earliest time a punch counts (e.g. 15:00). Empty = any time."),
    )
    window_end = models.TimeField(
        null=True, blank=True,
        help_text=_("Latest time a punch counts (e.g. 17:00). Empty = any time."),
    )
    working_weekdays = models.CharField(
        max_length=20, default="0,1,2,3,4,5",
        help_text=_("Comma-separated weekday numbers, Monday=0 ... Sunday=6."),
    )

    class Meta:
        verbose_name = _("Attendance rule")
        verbose_name_plural = _("Attendance rules")

    def __str__(self):
        return self.name

    def weekday_set(self):
        return {int(x) for x in self.working_weekdays.split(",") if x.strip() != ""}

    def clean(self):
        super().clean()
        target = {
            self.SHIFT: self.shift, self.DEPARTMENT: self.department,
            self.CATEGORY: self.employee_type, self.EMPLOYEE: self.employee,
        }
        if self.scope != self.GLOBAL and target[self.scope] is None:
            raise ValidationError(_("Choose the %(s)s this rule applies to.") % {"s": self.get_scope_display().lower()})
        try:
            days = self.weekday_set()
        except ValueError as exc:
            raise ValidationError({"working_weekdays": _("Use numbers 0-6 separated by commas.")}) from exc
        if not days or any(d < 0 or d > 6 for d in days):
            raise ValidationError({"working_weekdays": _("Use numbers 0-6 separated by commas.")})
        if bool(self.window_start) != bool(self.window_end):
            raise ValidationError(_("Set both window start and end, or neither."))
        if self.window_start and self.window_end and self.window_start > self.window_end:
            raise ValidationError(_("Window start must not be after window end."))


class AttendanceApproval(models.Model):
    """An approved date range. Approving locks its days; reopening needs a reason."""

    APPROVED, REOPENED = "approved", "reopened"
    start_date = models.DateField()
    end_date = models.DateField()
    department = models.ForeignKey(
        "base.Department", null=True, blank=True, on_delete=models.SET_NULL,
        help_text=_("Set when only one department was approved."),
    )
    status = models.CharField(
        max_length=10, default=APPROVED,
        choices=[(APPROVED, _("Approved")), (REOPENED, _("Reopened"))],
    )
    day_count = models.PositiveIntegerField(default=0)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    approved_at = models.DateTimeField(default=timezone.now)
    reopened_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="+",
    )
    reopened_at = models.DateTimeField(null=True, blank=True)
    reopen_reason = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-approved_at", "-id"]
        permissions = [
            ("approve_attendance", "Can approve attendance"),
            ("reopen_attendance", "Can reopen approved attendance"),
        ]

    def __str__(self):
        return f"{self.start_date} to {self.end_date} ({self.status})"


class DailyAttendance(models.Model):
    employee = models.ForeignKey(
        Employee, on_delete=models.CASCADE, related_name="daily_attendance"
    )
    date = models.DateField()
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, db_index=True)
    raw_count = models.PositiveIntegerField(default=0)
    first_punch = models.DateTimeField(null=True, blank=True)
    last_punch = models.DateTimeField(null=True, blank=True)
    flags = models.JSONField(default=list, blank=True)
    source = models.CharField(
        max_length=10, default="biometric",
        choices=[("biometric", _("Biometric")), ("correction", _("HR correction"))],
    )
    approval = models.ForeignKey(
        AttendanceApproval, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="days",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["employee", "date"], name="uniq_daily_attendance")
        ]
        indexes = [models.Index(fields=["date"])]
        ordering = ["date", "employee_id"]
        permissions = [
            ("process_attendance", "Can process attendance from biometric punches"),
            ("correct_attendance", "Can correct daily attendance"),
        ]

    @property
    def is_locked(self):
        return self.approval_id is not None

    def __str__(self):
        return f"{self.employee} {self.date} {self.status}"


class AttendanceCorrection(models.Model):
    """Audit trail of every manual change. Never edited or deleted."""

    daily = models.ForeignKey(DailyAttendance, on_delete=models.CASCADE, related_name="corrections")
    old_status = models.CharField(max_length=10, blank=True, default="")
    new_status = models.CharField(max_length=10)
    reason = models.TextField()
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at", "-id"]
