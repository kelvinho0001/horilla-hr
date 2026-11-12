from datetime import date, datetime, timedelta
from urllib.parse import urlencode

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST
import calendar

from base.models import Department, EmployeeType
from biometric_integration.models import RawTransaction
from employee.models import Employee
from horilla.decorators import login_required, permission_required

from . import calculators, services
from .forms import RuleForm, WindowFormSet
from .models import (
    STATUS_CHOICES, UNRESOLVED, AttendanceApproval, AttendanceRule, DailyAttendance,
    CORRECTABLE,
)

T = "attendance_processing/"


def _parse_date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _parse_month(value, last):
    try:
        y, m = (int(x) for x in value.split("-"))
        return date(y, m, calendar.monthrange(y, m)[1] if last else 1)
    except (AttributeError, TypeError, ValueError):
        return None


def get_range(params):
    """
    Any range: exact dates (start/end) or whole months (from_month/to_month).
    Defaults to the last 7 days.
    """
    start, end = _parse_date(params.get("start")), _parse_date(params.get("end"))
    if not (start and end):
        start = _parse_month(params.get("from_month"), last=False)
        end = _parse_month(params.get("to_month") or params.get("from_month"), last=True)
    if not (start and end):
        end = services.today()
        start = end - timedelta(days=6)
    return start, end


def _back(request):
    nxt = request.POST.get("next", "")
    if nxt and url_has_allowed_host_and_scheme(nxt, {request.get_host()}):
        return redirect(nxt)
    return redirect("att-proc-review")


def _scope(params):
    dept = Department.objects.filter(pk=params.get("department") or 0).first()
    etype = EmployeeType.objects.filter(pk=params.get("employee_type") or 0).first()
    return dept, etype


@login_required
@permission_required("attendance_processing.view_dailyattendance")
def review(request):
    start, end = get_range(request.GET)
    error = None
    if end < start:
        error = _("End date is before start date.")
    elif (end - start).days + 1 > services.MAX_RANGE_DAYS:
        error = _("Choose a range of at most %(n)s days.") % {"n": services.MAX_RANGE_DAYS}
    dept, etype = _scope(request.GET)
    ctx = {
        "start": start, "end": end, "error": error, "departments": Department.objects.all(),
        "employee_types": EmployeeType.objects.all(), "dept": dept, "etype": etype,
        "only_exceptions": request.GET.get("exceptions") == "1",
        "statuses": STATUS_CHOICES, "correctable": [s for s in STATUS_CHOICES if s[0] in CORRECTABLE],
        "query": request.GET.urlencode(),
        "can_process": request.user.has_perm("attendance_processing.process_attendance"),
        "can_correct": request.user.has_perm("attendance_processing.correct_attendance"),
        "can_approve": request.user.has_perm("attendance_processing.approve_attendance"),
    }
    if error:
        return render(request, T + "review.html", ctx)

    emps = list(services.employees_for_attendance(dept, etype))
    days = list(calculators.daterange(start, end))
    by_key = {
        (d.employee_id, d.date): d
        for d in DailyAttendance.objects.filter(employee__in=emps, date__range=(start, end))
    }
    weeks = calculators.week_buckets(start, end)
    rows, totals = [], {"present": 0, "unresolved": 0}
    for e in emps:
        cells = [by_key.get((e.pk, d)) for d in days]
        present = sum(1 for c in cells if c and c.status == "present")
        unresolved = sum(1 for c in cells if c and c.status in UNRESOLVED)
        wk = [
            sum(1 for d, c in zip(days, cells) if c and c.status == "present" and a <= d <= b)
            for a, b in weeks
        ]
        if ctx["only_exceptions"] and not unresolved:
            continue
        rows.append({
            "employee": e, "cells": list(zip(days, cells)), "present": present,
            "absent": sum(1 for c in cells if c and c.status == "absent"),
            "leave": sum(1 for c in cells if c and c.status == "leave"),
            "unresolved": unresolved, "weeks": wk,
        })
        totals["present"] += present
        totals["unresolved"] += unresolved
    ctx.update(
        rows=rows, days=days, weeks=[(i + 1, a, b) for i, (a, b) in enumerate(weeks)],
        totals=totals,
        approvals=AttendanceApproval.objects.filter(
            start_date__lte=end, end_date__gte=start
        )[:5],
    )
    return render(request, T + "review.html", ctx)


@login_required
@permission_required("attendance_processing.process_attendance")
@require_POST
def process(request):
    start, end = get_range(request.POST)
    dept, etype = _scope(request.POST)
    try:
        stats = services.process_range(start, end, services.employees_for_attendance(dept, etype))
    except services.AttendanceError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            _("Processed %(d)s days (%(c)s new, %(u)s updated, %(l)s locked/corrected skipped).")
            % {"d": stats["days"], "c": stats["created"], "u": stats["updated"], "l": stats["skipped_locked"]},
        )
    return _back(request)


@login_required
@permission_required("attendance_processing.correct_attendance")
@require_POST
def correct(request):
    emp = get_object_or_404(Employee, pk=request.POST.get("employee"))
    day = _parse_date(request.POST.get("date"))
    try:
        if not day:
            raise services.AttendanceError(_("Invalid date."))
        services.correct(emp, day, request.POST.get("status"), request.POST.get("reason"), request.user)
        messages.success(request, _("Attendance corrected."))
    except services.AttendanceError as exc:
        messages.error(request, str(exc))
    return _back(request)


@login_required
@permission_required("attendance_processing.correct_attendance")
@require_POST
def bulk_absent(request):
    start, end = get_range(request.POST)
    dept, etype = _scope(request.POST)
    try:
        n = services.bulk_mark_missing_absent(
            services.employees_for_attendance(dept, etype), start, end,
            request.POST.get("reason"), request.user,
        )
        messages.success(request, _("%(n)s missing days marked absent.") % {"n": n})
    except services.AttendanceError as exc:
        messages.error(request, str(exc))
    return _back(request)


@login_required
@permission_required("attendance_processing.approve_attendance")
@require_POST
def approve(request):
    start, end = get_range(request.POST)
    dept, _etype = _scope(request.POST)
    try:
        a = services.approve(start, end, request.user, department=dept)
        messages.success(request, _("Approved %(n)s days. They are now locked.") % {"n": a.day_count})
    except services.ApprovalBlocked as exc:
        messages.error(
            request,
            _("%(n)s day(s) still need review (missing or outside the attendance window). Resolve them first.")
            % {"n": len(exc.rows)},
        )
    except services.AttendanceError as exc:
        messages.error(request, str(exc))
    return _back(request)


@login_required
@permission_required("attendance_processing.view_attendanceapproval")
def approvals(request):
    return render(request, T + "approvals.html", {
        "approvals": AttendanceApproval.objects.select_related("approved_by", "reopened_by", "department")[:100],
        "can_reopen": request.user.has_perm("attendance_processing.reopen_attendance"),
    })


@login_required
@permission_required("attendance_processing.reopen_attendance")
@require_POST
def reopen(request, pk):
    approval = get_object_or_404(AttendanceApproval, pk=pk)
    try:
        services.reopen(approval, request.user, request.POST.get("reason"))
        messages.success(request, _("Approval reopened. Its days can be corrected again."))
    except services.AttendanceError as exc:
        messages.error(request, str(exc))
    return redirect("att-proc-approvals")


@login_required
@permission_required("attendance_processing.view_dailyattendance")
def day_detail(request, employee_id, day):
    emp = get_object_or_404(Employee, pk=employee_id)
    d = _parse_date(day) or services.today()
    daily = DailyAttendance.objects.filter(employee=emp, date=d).first()
    tz = services.attendance_tz()
    punches = [
        t for t in RawTransaction.objects.filter(employee=emp).order_by("attendance_timestamp")
        if t.attendance_timestamp.astimezone(tz).date() == d
    ]
    return render(request, T + "day_detail.html", {
        "emp": emp, "day": d, "daily": daily, "punches": punches, "tz": tz,
        "corrections": daily.corrections.select_related("user") if daily else [],
    })


@login_required
@permission_required("attendance_processing.view_attendancerule")
def rule_list(request):
    return render(request, T + "rules.html", {
        "rules": AttendanceRule.objects.prefetch_related("windows"),
        "can_edit": request.user.has_perm("attendance_processing.add_attendancerule"),
    })


@login_required
@permission_required("attendance_processing.add_attendancerule")
def rule_form(request, pk=None):
    rule = get_object_or_404(AttendanceRule, pk=pk) if pk else None
    form = RuleForm(request.POST or None, instance=rule)
    formset = WindowFormSet(request.POST or None, instance=rule or AttendanceRule())
    if request.method == "POST" and form.is_valid():
        saved = form.save(commit=False)
        formset = WindowFormSet(request.POST, instance=saved)
        if formset.is_valid():
            saved.save()
            formset.instance = saved
            formset.save()
            messages.success(request, _("Rule saved. Re-process attendance to apply it."))
            return redirect("att-proc-rules")
    return render(request, T + "rule_form.html", {"form": form, "formset": formset})
