import calendar
from datetime import date, datetime, timedelta

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from attendance.models import Attendance
from base.models import Department, EmployeeType
from biometric_integration.models import RawTransaction
from employee.models import Employee
from horilla.decorators import login_required, permission_required

from . import calculators, services
from .forms import RuleForm, WindowFormSet
from .models import AttendanceRule

T = "attendance_processing/"
PROCESS = "attendance_processing.process_attendance"


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
    """Any range: exact dates (start/end) or whole months (from_month/to_month). Default: last 7 days."""
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
@permission_required(PROCESS)
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
        "only_attention": request.GET.get("attention") == "1",
    }
    if error:
        return render(request, T + "review.html", ctx)

    emps = list(services.employees_for_attendance(dept, etype))
    days = list(calculators.daterange(start, end))
    info = services.classify_range(emps, start, end)
    horilla = {
        (a.employee_id_id, a.attendance_date): a
        for a in Attendance.objects.filter(employee_id__in=emps, attendance_date__range=(start, end))
    }
    weeks = calculators.week_buckets(start, end)
    rows, totals = [], {"validated": 0, "waiting": 0, "attention": 0}
    for e in emps:
        cells, n_val, n_wait, n_att = [], 0, 0, 0
        for d in days:
            att, inf = horilla.get((e.pk, d)), info.get((e.pk, d))
            if att is not None:
                state = "validated" if att.attendance_validated else "waiting"
                n_val += state == "validated"
                n_wait += state == "waiting"
            elif inf is not None and inf.status == calculators.ATTENTION:
                state = "attention"
                n_att += 1
            else:
                state = None
            cells.append((d, state))
        if ctx["only_attention"] and not n_att:
            continue
        wk = [sum(1 for (d, s) in cells if s == "validated" and a <= d <= b) for a, b in weeks]
        rows.append({"employee": e, "cells": cells, "validated": n_val, "waiting": n_wait, "attention": n_att, "weeks": wk})
        totals["validated"] += n_val
        totals["waiting"] += n_wait
        totals["attention"] += n_att
    ctx.update(rows=rows, days=days, weeks=[(i + 1, a, b) for i, (a, b) in enumerate(weeks)], totals=totals,
               query=request.GET.urlencode(), can_process=request.user.has_perm(PROCESS))
    return render(request, T + "review.html", ctx)


@login_required
@permission_required(PROCESS)
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
            _("%(c)s attendance records added to Attendance > Attendances (waiting for validation); "
              "%(a)s already there; %(n)s need attention.")
            % {"c": stats["created"], "a": stats["already_there"], "n": len(stats["attention"])},
        )
    return _back(request)


@login_required
@permission_required(PROCESS)
@require_POST
def accept(request):
    emp = get_object_or_404(Employee, pk=request.POST.get("employee"))
    day = _parse_date(request.POST.get("date"))
    try:
        if not day:
            raise services.AttendanceError(_("Invalid date."))
        services.accept_day(emp, day)
        messages.success(request, _("Added to Attendance > Attendances, waiting for validation."))
    except services.AttendanceError as exc:
        messages.error(request, str(exc))
    return _back(request)


@login_required
@permission_required(PROCESS)
def day_detail(request, employee_id, day):
    emp = get_object_or_404(Employee, pk=employee_id)
    d = _parse_date(day) or services.today()
    tz = services.attendance_tz()
    punches = [
        t for t in RawTransaction.objects.filter(employee=emp).order_by("attendance_timestamp")
        if t.attendance_timestamp.astimezone(tz).date() == d
    ]
    info = services.classify_range([emp], d, d).get((emp.pk, d))
    return render(request, T + "day_detail.html", {
        "emp": emp, "day": d, "punches": punches, "tz": tz, "info": info,
        "attendance": Attendance.objects.filter(employee_id=emp, attendance_date=d).first(),
        "can_accept": request.user.has_perm(PROCESS),
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
            messages.success(request, _("Rule saved."))
            return redirect("att-proc-rules")
    return render(request, T + "rule_form.html", {"form": form, "formset": formset})
