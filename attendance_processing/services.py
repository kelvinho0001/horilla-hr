"""Turn biometric punches into ordinary Horilla attendance records."""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from attendance.models import Attendance
from biometric_integration.models import ExternalEmployeeMapping, RawTransaction
from employee.models import Employee

from . import calculators
from .models import AttendanceRule

MAX_RANGE_DAYS = 93
MIN_GAP_FOR_CHECKOUT = timedelta(hours=1)


class AttendanceError(Exception):
    pass


@dataclass
class DayInfo:
    status: str  # calculators.OK or calculators.ATTENTION
    flags: list = field(default_factory=list)
    count: int = 0
    first: datetime = None
    last: datetime = None


def attendance_tz():
    return ZoneInfo(getattr(settings, "ATTENDANCE_TIMEZONE", "Africa/Lagos"))


def today():
    return timezone.now().astimezone(attendance_tz()).date()


def employees_for_attendance(department=None, employee_type=None):
    """Active employees that have a verified, active biometric mapping."""
    ids = ExternalEmployeeMapping.objects.filter(
        is_active=True, status=ExternalEmployeeMapping.VERIFIED, employee__is_active=True
    ).values_list("employee_id", flat=True)
    qs = Employee.objects.filter(pk__in=ids).select_related(
        "employee_work_info", "employee_work_info__department_id"
    )
    if department:
        qs = qs.filter(employee_work_info__department_id=department)
    if employee_type:
        qs = qs.filter(employee_work_info__employee_type_id=employee_type)
    return qs.order_by("badge_id", "employee_first_name")


class RuleResolver:
    """Most specific active rule: employee > category > department > shift > global."""

    def __init__(self):
        self.rules = list(AttendanceRule.objects.filter(is_active=True).prefetch_related("windows"))

    def for_employee(self, emp):
        wi = getattr(emp, "employee_work_info", None)
        dept = getattr(wi, "department_id_id", None)
        etype = getattr(wi, "employee_type_id_id", None)
        shift = getattr(wi, "shift_id_id", None)
        order = [
            (AttendanceRule.EMPLOYEE, lambda r: r.employee_id == emp.pk),
            (AttendanceRule.CATEGORY, lambda r: etype and r.employee_type_id == etype),
            (AttendanceRule.DEPARTMENT, lambda r: dept and r.department_id == dept),
            (AttendanceRule.SHIFT, lambda r: shift and r.shift_id == shift),
            (AttendanceRule.GLOBAL, lambda r: True),
        ]
        for scope, match in order:
            for rule in self.rules:
                if rule.scope == scope and match(rule):
                    return rule
        return None


def _check_range(start, end):
    if end < start:
        raise AttendanceError("End date is before start date.")
    if (end - start).days + 1 > MAX_RANGE_DAYS:
        raise AttendanceError(f"Choose a range of at most {MAX_RANGE_DAYS} days.")


def classify_range(employees, start, end):
    """
    {(employee_id, date): DayInfo} for every day that has punches, using the attendance rules.
    The day is the calendar date (Africa/Lagos) of the punch. Days before joining or after the
    contract end are ignored. Nothing is written.
    """
    _check_range(start, end)
    end = min(end, today())
    employees = list(employees)
    result = {}
    if end < start or not employees:
        return result
    tz = attendance_tz()
    lo = datetime.combine(start, time.min, tzinfo=tz) - timedelta(days=1)
    hi = datetime.combine(end, time.max, tzinfo=tz) + timedelta(days=1)
    punches = defaultdict(list)
    for t in RawTransaction.objects.filter(
        employee__in=employees, attendance_timestamp__range=(lo, hi)
    ).only("employee_id", "attendance_timestamp", "source_timezone"):
        local = t.attendance_timestamp.astimezone(ZoneInfo(t.source_timezone) if t.source_timezone else tz)
        if start <= local.date() <= end:
            punches[(t.employee_id, local.date())].append(local)
    rules = RuleResolver()
    by_id = {e.pk: e for e in employees}
    for (emp_id, day), items in punches.items():
        emp = by_id[emp_id]
        wi = getattr(emp, "employee_work_info", None)
        joined, left = getattr(wi, "date_joining", None), getattr(wi, "contract_end_date", None)
        if (joined and day < joined) or (left and day > left):
            continue
        items.sort()
        rule = rules.for_employee(emp)
        status, flags = calculators.classify([p.time() for p in items], rule.window_list() if rule else [])
        result[(emp_id, day)] = DayInfo(status, flags, len(items), items[0], items[-1])
    return result


def _create_attendance(emp, day, info):
    """One ordinary (unvalidated) Horilla Attendance record for a worker's day."""
    wi = getattr(emp, "employee_work_info", None)
    clock_out = None
    if info.count > 1 and info.last - info.first >= MIN_GAP_FOR_CHECKOUT:
        clock_out = info.last
    return Attendance.objects.create(
        employee_id=emp,
        attendance_date=day,
        attendance_clock_in_date=info.first.date(),
        attendance_clock_in=info.first.time().replace(microsecond=0),
        attendance_clock_out_date=clock_out.date() if clock_out else None,
        attendance_clock_out=clock_out.time().replace(microsecond=0) if clock_out else None,
        shift_id=getattr(wi, "shift_id", None),
        work_type_id=getattr(wi, "work_type_id", None),
        attendance_validated=False,
    )


def process_range(start, end, employees=None):
    """
    Add one Horilla Attendance record (waiting for validation) for every worker-day with a valid
    punch that has no record yet. Existing records (validated or not, manual or earlier) are never
    touched, so it is safe to run again after each upload. Returns counts and the days that need
    attention (outside the windows, or punches in two shifts), which are NOT added.
    """
    emps = list(employees if employees is not None else employees_for_attendance())
    days = classify_range(emps, start, end)
    by_id = {e.pk: e for e in emps}
    existing = set(
        Attendance.objects.filter(employee_id__in=by_id, attendance_date__range=(start, end)).values_list("employee_id", "attendance_date")
    )
    stats = {"created": 0, "already_there": 0, "attention": []}
    for (emp_id, day), info in sorted(days.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        if info.status == calculators.ATTENTION:
            if (emp_id, day) not in existing:
                stats["attention"].append((by_id[emp_id], day, info))
            continue
        if (emp_id, day) in existing:
            stats["already_there"] += 1
            continue
        try:
            with transaction.atomic():
                _create_attendance(by_id[emp_id], day, info)
            stats["created"] += 1
        except IntegrityError:  # created by a concurrent run
            stats["already_there"] += 1
    return stats


def accept_day(employee, day):
    """HR decided a flagged day is real attendance: add it (waiting for validation)."""
    days = classify_range([employee], day, day)
    info = days.get((employee.pk, day))
    if info is None:
        raise AttendanceError("There are no punches for that worker on that day.")
    if Attendance.objects.filter(employee_id=employee, attendance_date=day).exists():
        raise AttendanceError("That day already has an attendance record.")
    return _create_attendance(employee, day, info)
