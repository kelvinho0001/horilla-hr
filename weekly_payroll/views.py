from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from horilla.decorators import login_required, permission_required

from . import services
from .export_xlsx import build_workbook
from .forms import RunForm
from .models import PayrollLine, PayrollRun

T = "weekly_payroll/"
P = "weekly_payroll."


def _guard(request, fn, *args, ok=None, **kw):
    try:
        result = fn(*args, **kw)
    except services.PayrollError as exc:
        messages.error(request, str(exc))
        return None
    if ok:
        messages.success(request, ok)
    return result


def _runs_for(user):
    qs = PayrollRun.objects.select_related("department", "employee_type")
    # Accountants see approved payroll only.
    if not user.has_perm(P + "add_payrollrun"):
        qs = qs.filter(status__in=PayrollRun.FINAL)
    return qs


@login_required
@permission_required(P + "view_payrollrun")
def run_list(request):
    return render(request, T + "run_list.html", {
        "runs": _runs_for(request.user)[:100],
        "can_add": request.user.has_perm(P + "add_payrollrun"),
    })


@login_required
@permission_required(P + "add_payrollrun")
def run_create(request):
    form = RunForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        run = form.save(commit=False)
        run.created_by = request.user
        run.save()
        _guard(request, services.generate, run, ok=_("Payroll calculated."))
        return redirect("wp-run-detail", pk=run.pk)
    return render(request, T + "run_form.html", {"form": form})


@login_required
@permission_required(P + "view_payrollrun")
def run_detail(request, pk):
    run = get_object_or_404(_runs_for(request.user), pk=pk)
    u = request.user
    lines = list(run.lines.all())
    return render(request, T + "run_detail.html", {
        "run": run, "lines": lines,
        "week_headers": range(1, max((len(l.week_days) for l in lines), default=0) + 1),
        "can_edit": u.has_perm(P + "change_payrollrun") and not run.is_final,
        "can_review": u.has_perm(P + "review_payrollrun") and run.status == "draft",
        "can_approve": u.has_perm(P + "approve_payrollrun") and run.status == "reviewed",
        "problems": services.blockers(run) if not run.is_final else [],
    })


@login_required
@permission_required(P + "change_payrollrun")
@require_POST
def run_recalculate(request, pk):
    run = get_object_or_404(PayrollRun, pk=pk)
    _guard(request, services.generate, run, ok=_("Recalculated."))
    return redirect("wp-run-detail", pk=pk)


@login_required
@permission_required(P + "change_payrollrun")
@require_POST
def line_working_days(request, pk):
    line = get_object_or_404(PayrollLine, pk=pk)
    try:
        days = int(request.POST.get("working_days", ""))
    except ValueError:
        messages.error(request, _("Enter a whole number of working days."))
    else:
        _guard(request, services.set_working_days, line, days, request.user)
    return redirect("wp-run-detail", pk=line.run_id)


@login_required
@permission_required(P + "review_payrollrun")
@require_POST
def run_review(request, pk):
    run = get_object_or_404(PayrollRun, pk=pk)
    _guard(request, services.mark_reviewed, run, request.user, ok=_("Marked as reviewed."))
    return redirect("wp-run-detail", pk=pk)


@login_required
@permission_required(P + "approve_payrollrun")
@require_POST
def run_approve(request, pk):
    run = get_object_or_404(PayrollRun, pk=pk)
    _guard(request, services.approve, run, request.user, ok=_("Payroll approved and locked."))
    return redirect("wp-run-detail", pk=pk)


@login_required
@permission_required(P + "view_payrollrun")
def run_download(request, pk):
    run = get_object_or_404(_runs_for(request.user), pk=pk)
    if not run.lines.exists():
        messages.error(request, _("Nothing to download yet. Calculate the payroll first."))
        return redirect("wp-run-detail", pk=pk)
    resp = HttpResponse(
        build_workbook(run),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    suffix = "" if run.is_final else "-DRAFT"
    resp["Content-Disposition"] = f'attachment; filename="payroll-{slugify(run.name)}{suffix}.xlsx"'
    return resp
