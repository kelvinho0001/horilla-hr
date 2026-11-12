"""
Attendance rules for biometric punches.

Raw punches live in biometric_integration.RawTransaction. "Process" turns the valid ones into ordinary
Horilla Attendance records (Attendance > Attendances), where HR validates them with Horilla's own
screens. A rule says which punch times count as attendance; nothing else is stored here.
"""

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from employee.models import Employee
from horilla.models import HorillaModel


class AttendanceRule(HorillaModel):
    """
    Which punch times count as attendance (one or more windows). The most specific active rule wins:
    employee > worker category > department > shift > everyone. With no rule, any punch time counts.
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

    class Meta:
        verbose_name = _("Attendance rule")
        verbose_name_plural = _("Attendance rules")
        permissions = [("process_attendance", "Can add biometric punches to Horilla attendance")]

    def __str__(self):
        return self.name

    def window_list(self):
        """[(start, end), ...]; empty means any punch time counts."""
        return [(w.start, w.end) for w in self.windows.all()]

    def clean(self):
        super().clean()
        target = {
            self.SHIFT: self.shift, self.DEPARTMENT: self.department,
            self.CATEGORY: self.employee_type, self.EMPLOYEE: self.employee,
        }
        if self.scope != self.GLOBAL and target[self.scope] is None:
            raise ValidationError(_("Choose the %(s)s this rule applies to.") % {"s": self.get_scope_display().lower()})


class AttendanceRuleWindow(models.Model):
    """
    A time-of-day range in which a punch counts as attendance (e.g. 00:00-09:00 for the
    night shift and 14:00-20:00 for the afternoon shift). A rule may have several; none = any time.
    """

    rule = models.ForeignKey(AttendanceRule, on_delete=models.CASCADE, related_name="windows")
    start = models.TimeField()
    end = models.TimeField()

    class Meta:
        ordering = ["start", "id"]

    def clean(self):
        if self.start and self.end and self.start >= self.end:
            raise ValidationError(_("A window must start before it ends (it cannot cross midnight; use two windows)."))

    def __str__(self):
        return f"{self.start:%H:%M}-{self.end:%H:%M}"
