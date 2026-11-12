"""Processing, correction, approval and reopening of daily attendance."""

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from base.models import Holidays
from biometric_integration.models import ExternalEmployeeMapping, RawTransaction
from employee.models import Employee

from . import calculators
from .models import (
    CORRECTABLE, UNRESOLVED, ABSENT, MISSING, AttendanceApproval, AttendanceCorrection,
    AttendanceRule, DailyAttendance,
)

# Used only when no rule applies at all. A global rule overrides it.
FALLBACK_WEEKDAYS = {0, 1, 2, 3, 4, 5}
MAX_RANGE_DAYS = 93


class AttendanceError(Exception):
    pass


class ApprovalBlocked(AttendanceError):
    def __init__(self, rows):
        self.rows = rows
        super().__init__(f"{len(rows)} day(s) still need review")


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


def _holiday_checker(start, end):
    qs = Holidays.objects.filter(is_active=True) if hasattr(Holidays, "is_active") else Holidays.objects.all()
    holidays = list(qs.prefetch_related("department", "job_position", "employees"))

    def covers(h, d):
        h_end = h.end_date or h.start_date
        if h.recurring:
            # compare month/day ignoring year (ranges inside one year only)
            try:
                s = h.start_date.replace(year=d.year)
                e = h_end.replace(year=d.year)
            except ValueError:  # 29 Feb
                return False
            return s <= d <= e
        return h.start_date <= d <= h_end

    def is_holiday(emp, d):
        wi = getattr(emp, "employee_work_info", None)
        for h in holidays:
            if not covers(h, d):
                continue
            if not h.is_specific:
                return True
            if h.assigning_type == "department" and wi and any(
                x.pk == wi.department_id_id for x in h.department.all()
            ):
                return True
            if h.assigning_type == "job_position" and wi and any(
                x.pk == wi.job_position_id_id for x in h.job_position.all()
            ):
                return True
            if h.assigning_type == "employee" and any(x.pk == emp.pk for x in h.employees.all()):
                return True
        return False

    return is_holiday


def _leave_checker(employee_ids, start, end):
    from leave.models import LeaveRequest

    spans = defaultdict(list)
    for lr in LeaveRequest.objects.filter(
        employee_id__in=employee_ids,
        status__in=["approved", "cancel_requested"],
        start_date__lte=end,
    ).filter(Q(end_date__gte=start) | Q(end_date__isnull=True)):
        spans[lr.employee_id_id].append((lr.start_date, lr.end_date or lr.start_date))

    return lambda emp, d: any(s <= d <= e for s, e in spans.get(emp.pk, ()))


def process_range(start, end, employees=None):
    """
    (Re)build daily rows from raw punches for ``start..end`` (capped at today).
    Idempotent. Approved days and HR-corrected days are never touched.
    """
    if end < start:
        raise AttendanceError("End date is before start date.")
    if (end - start).days + 1 > MAX_RANGE_DAYS:
        raise AttendanceError(f"Choose a range of at most {MAX_RANGE_DAYS} days.")
    end = min(end, today())
    stats = {"created": 0, "updated": 0, "skipped_locked": 0, "days": 0}
    if end < start:
        return stats
    emps = list(employees if employees is not None else employees_for_attendance())
    if not emps:
        return stats

    tz = attendance_tz()
    lo = datetime.combine(start, time.min, tzinfo=tz) - timedelta(days=1)
    hi = datetime.combine(end, time.max, tzinfo=tz) + timedelta(days=1)
    punches = defaultdict(list)
    for t in RawTransaction.objects.filter(
        employee__in=emps, attendance_timestamp__range=(lo, hi)
    ).only("employee_id", "attendance_timestamp", "source_timezone"):
        local = t.attendance_timestamp.astimezone(
            ZoneInfo(t.source_timezone) if t.source_timezone else tz
        )
        if start <= local.date() <= end:
            punches[(t.employee_id, local.date())].append((local, t.attendance_timestamp))

    existing = {
        (d.employee_id, d.date): d
        for d in DailyAttendance.objects.filter(employee__in=emps, date__range=(start, end))
    }
    rules = RuleResolver()
    is_holiday = _holiday_checker(start, end)
    on_leave = _leave_checker([e.pk for e in emps], start, end)

    for emp in emps:
        wi = getattr(emp, "employee_work_info", None)
        joined = getattr(wi, "date_joining", None)
        left = getattr(wi, "contract_end_date", None)
        rule = rules.for_employee(emp)
        window = rule.window_list() if rule else []
        weekdays = rule.weekday_set() if rule else FALLBACK_WEEKDAYS
        for d in calculators.daterange(start, end):
            if (joined and d < joined) or (left and d > left):
                continue
            row = existing.get((emp.pk, d))
            if row and (row.is_locked or row.source == "correction"):
                stats["skipped_locked"] += 1
                continue
            items = sorted(punches.get((emp.pk, d), []), key=lambda p: p[1])
            status, flags = calculators.classify(
                [p[0].time() for p in items],
                window=window,
                working_weekday=d.weekday() in weekdays,
                is_holiday=is_holiday(emp, d),
                on_leave=on_leave(emp, d),
            )
            values = {
                "status": status,
                "raw_count": len(items),
                "first_punch": items[0][1] if items else None,
                "last_punch": items[-1][1] if items else None,
                "flags": flags,
            }
            stats["days"] += 1
            if row:
                for k, v in values.items():
                    setattr(row, k, v)
                row.save()
                stats["updated"] += 1
            else:
                try:
                    with transaction.atomic():
                        DailyAttendance.objects.create(employee=emp, date=d, **values)
                    stats["created"] += 1
                except IntegrityError:
                    # A concurrent run created it first; update instead.
                    DailyAttendance.objects.filter(employee=emp, date=d, approval__isnull=True).exclude(
                        source="correction"
                    ).update(**values)
                    stats["updated"] += 1
    return stats


@transaction.atomic
def correct(employee, day, new_status, reason, user):
    """HR correction of one day. Reason is mandatory; approved days must be reopened first."""
    reason = (reason or "").strip()
    if not reason:
        raise AttendanceError("A reason is required for every correction.")
    if new_status not in CORRECTABLE:
        raise AttendanceError("That status cannot be set by hand.")
    if day > today():
        raise AttendanceError("Future dates cannot be marked.")
    row = DailyAttendance.objects.select_for_update().filter(employee=employee, date=day).first()
    if row is None:
        row = DailyAttendance.objects.create(employee=employee, date=day, status=new_status, source="correction")
        old = ""
    else:
        if row.is_locked:
            raise AttendanceError("This day is in an approved period. Reopen the period first.")
        old = row.status
    row.status, row.source = new_status, "correction"
    row.save()
    AttendanceCorrection.objects.create(
        daily=row, old_status=old, new_status=new_status, reason=reason, user=user
    )
    return row


@transaction.atomic
def bulk_mark_missing_absent(employees, start, end, reason, user):
    rows = DailyAttendance.objects.filter(
        employee__in=list(employees), date__range=(start, end), status=MISSING, approval__isnull=True
    )
    n = 0
    for row in rows:
        correct(row.employee, row.date, ABSENT, reason, user)
        n += 1
    return n


@transaction.atomic
def approve(start, end, user, department=None):
    if end < start:
        raise AttendanceError("End date is before start date.")
    rows = DailyAttendance.objects.select_for_update().filter(
        date__range=(start, end), approval__isnull=True
    )
    if department:
        rows = rows.filter(employee__employee_work_info__department_id=department)
    rows = list(rows.select_related("employee"))
    if not rows:
        raise AttendanceError("There is no unapproved attendance in this range. Process it first.")
    blockers = [r for r in rows if r.status in UNRESOLVED]
    if blockers:
        raise ApprovalBlocked(blockers)
    approval = AttendanceApproval.objects.create(
        start_date=start, end_date=end, department=department, approved_by=user,
        day_count=len(rows),
    )
    DailyAttendance.objects.filter(pk__in=[r.pk for r in rows]).update(approval=approval)
    return approval


@transaction.atomic
def reopen(approval, user, reason):
    reason = (reason or "").strip()
    if not reason:
        raise AttendanceError("A reason is required to reopen approved attendance.")
    approval = AttendanceApproval.objects.select_for_update().get(pk=approval.pk)
    if approval.status != AttendanceApproval.APPROVED:
        raise AttendanceError("This approval is already reopened.")
    approval.days.update(approval=None)
    approval.status = AttendanceApproval.REOPENED
    approval.reopened_by, approval.reopened_at, approval.reopen_reason = (
        user, timezone.now(), reason,
    )
    approval.save()
    return approval
