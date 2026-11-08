from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from horilla.decorators import login_required, permission_required

from . import services
from .export_xlsx import build_workbook
from .forms import AgreementForm, FineForm, FineTypeForm, ProfileForm, RunForm
from .models import Fine, FineType, LoanAgreement, PayrollRun, WorkerPayProfile

T = "weekly_payroll/"
P = "weekly_payroll."


def _guard(request, fn, *args, ok=None, back=None, **kw):
    try:
        result = fn(*args, **kw)
    except services.PayrollError as exc:
        messages.error(request, str(exc))
        return None
    if ok:
        messages.success(request, ok)
    return result


def _runs_for(user):
    qs = PayrollRun.objects.select_related("department")
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
        if _guard(request, services.generate, run, ok=_("Payroll calculated from approved attendance.")):
            pass
        return redirect("wp-run-detail", pk=run.pk)
    return render(request, T + "run_form.html", {"form": form})


@login_required
@permission_required(P + "view_payrollrun")
def run_detail(request, pk):
    run = get_object_or_404(_runs_for(request.user), pk=pk)
    u = request.user
    return render(request, T + "run_detail.html", {
        "run": run, "lines": run.lines.all(),
        "week_headers": range(1, max((len(l.week_days) for l in run.lines.all()), default=0) + 1),
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
    from .models import PayrollLine

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


# ---- pay profiles
@login_required
@permission_required(P + "view_workerpayprofile")
def profile_list(request):
    return render(request, T + "profiles.html", {
        "profiles": WorkerPayProfile.objects.select_related("employee"),
        "can_add": request.user.has_perm(P + "add_workerpayprofile"),
        "can_approve": request.user.has_perm(P + "approve_workerpayprofile"),
    })


@login_required
@permission_required(P + "add_workerpayprofile")
def profile_create(request):
    form = ProfileForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        p = form.save(commit=False)
        p.created_by = request.user
        p.save()
        messages.success(request, _("Saved. It counts in payroll once Management approves it."))
        return redirect("wp-profiles")
    return render(request, T + "simple_form.html", {"form": form, "title": _("New pay profile")})


@login_required
@permission_required(P + "approve_workerpayprofile")
@require_POST
def profile_decide(request, pk, decision):
    from django.utils import timezone

    p = get_object_or_404(WorkerPayProfile, pk=pk, status="pending")
    p.status = "approved" if decision == "approve" else "rejected"
    p.approved_by, p.approved_at = request.user, timezone.now()
    p.save()
    return redirect("wp-profiles")


# ---- fines
@login_required
@permission_required(P + "view_fine")
def fine_list(request):
    return render(request, T + "fines.html", {
        "fines": Fine.objects.select_related("employee", "fine_type", "applied_run"),
        "can_add": request.user.has_perm(P + "add_fine"),
        "can_approve": request.user.has_perm(P + "approve_fine"),
        "can_waive": request.user.has_perm(P + "waive_fine"),
        "fine_types": FineType.objects.filter(is_active=True),
    })


@login_required
@permission_required(P + "add_fine")
def fine_create(request):
    ft = FineType.objects.filter(pk=request.GET.get("type") or 0).first()
    form = FineForm(request.POST or None, initial={"fine_type": ft, "amount": ft.default_amount if ft else None,
                                                    "description": ft.name if ft else ""})
    if request.method == "POST" and form.is_valid():
        f = form.save(commit=False)
        f.issued_by = request.user
        f.save()
        messages.success(request, _("Fine recorded. It is deducted once Management approves it."))
        return redirect("wp-fines")
    return render(request, T + "simple_form.html", {"form": form, "title": _("Issue a fine")})


@login_required
@permission_required(P + "add_finetype")
def fine_type_create(request):
    form = FineTypeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("wp-fines")
    return render(request, T + "simple_form.html", {"form": form, "title": _("New fine type")})


@login_required
@permission_required(P + "approve_fine")
@require_POST
def fine_approve(request, pk):
    _guard(request, services.approve_fine, get_object_or_404(Fine, pk=pk), request.user, ok=_("Fine approved."))
    return redirect("wp-fines")


@login_required
@permission_required(P + "waive_fine")
@require_POST
def fine_waive(request, pk):
    _guard(request, services.waive_fine, get_object_or_404(Fine, pk=pk), request.user,
           request.POST.get("reason"), ok=_("Fine waived."))
    return redirect("wp-fines")


# ---- loans
@login_required
@permission_required(P + "view_loanagreement")
def loan_list(request):
    return render(request, T + "loans.html", {
        "agreements": LoanAgreement.objects.select_related("loan", "loan__employee_id"),
        "can_add": request.user.has_perm(P + "add_loanagreement"),
        "can_approve": request.user.has_perm(P + "approve_loanagreement"),
    })


@login_required
@permission_required(P + "add_loanagreement")
def loan_create(request):
    form = AgreementForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        a = form.save(commit=False)
        a.created_by = request.user
        a.save()
        messages.success(request, _("Agreement saved. Management must approve it before deductions start."))
        return redirect("wp-loans")
    return render(request, T + "simple_form.html", {
        "form": form, "title": _("Loan repayment agreement"),
        "help": _("Create the loan itself in Payroll > Loans first, then set its repayment terms here."),
    })


@login_required
@permission_required(P + "approve_loanagreement")
@require_POST
def loan_approve(request, pk):
    _guard(request, services.approve_agreement, get_object_or_404(LoanAgreement, pk=pk), request.user,
           ok=_("Agreement approved."))
    return redirect("wp-loans")


@login_required
@permission_required(P + "approve_loanagreement")
@require_POST
def loan_adjust(request, pk):
    from .money import D

    a = get_object_or_404(LoanAgreement, pk=pk)
    try:
        amount = D(request.POST.get("amount") or 0)
    except Exception:
        messages.error(request, _("Invalid amount."))
        return redirect("wp-loans")
    _guard(request, services.record_adjustment, a, request.POST.get("kind"), amount,
           request.POST.get("note"), request.user, ok=_("Recorded."))
    return redirect("wp-loans")
