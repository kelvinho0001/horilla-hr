from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from base.models import Department
from employee.models import Employee
from horilla.decorators import login_required, permission_required
from weekly_payroll.models import PayrollRun

from . import services
from .forms import BankUploadForm, WorkerBankForm
from .models import Bank, ExportBatch

T = "bank_export/"
P = "bank_export."


@login_required
@permission_required(P + "view_exportbatch")
def dashboard(request):
    runs = list(PayrollRun.objects.filter(status__in=PayrollRun.FINAL).order_by("-end_date"))
    live = {b.run_id: b for b in ExportBatch.objects.filter(status__in=["generated", "paid"])}
    rows = []
    for r in runs:
        pf = services.preflight(r) if r.status == PayrollRun.APPROVED else None
        rows.append({"run": r, "export": live.get(r.pk), "pf": pf})
    return render(request, T + "dashboard.html", {
        "rows": rows, "history": ExportBatch.objects.select_related("run", "created_by")[:50],
    })


@login_required
@permission_required(P + "view_exportbatch")
def run_export(request, pk):
    run = get_object_or_404(PayrollRun, pk=pk, status__in=PayrollRun.FINAL)
    pf = services.preflight(run) if run.status == PayrollRun.APPROVED else None
    return render(request, T + "run_export.html", {
        "run": run, "pf": pf, "narration": services.default_narration(run),
        "batches": run.exports.select_related("created_by"),
        "can_generate": request.user.has_perm(P + "generate_export") and run.status == PayrollRun.APPROVED,
        "can_supersede": request.user.has_perm(P + "supersede_export"),
        "can_pay": request.user.has_perm(P + "mark_export_paid"),
    })


@login_required
@permission_required(P + "generate_export")
@require_POST
def generate(request, pk):
    run = get_object_or_404(PayrollRun, pk=pk)
    try:
        batch = services.export_run(run, request.user, allow_partial=request.POST.get("partial") == "1")
        messages.success(request, _("Payment file %(r)s generated.") % {"r": batch.reference})
    except services.ExportError as exc:
        messages.error(request, str(exc))
    return redirect("bx-run", pk=pk)


@login_required
@permission_required(P + "view_exportbatch")
def download(request, pk):
    batch = get_object_or_404(ExportBatch, pk=pk)
    ExportBatch.objects.filter(pk=pk).update(download_count=batch.download_count + 1)
    resp = HttpResponse(
        bytes(batch.content),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    resp["Content-Disposition"] = f'attachment; filename="{batch.reference}.xlsx"'
    return resp


@login_required
@permission_required(P + "supersede_export")
@require_POST
def supersede(request, pk):
    batch = get_object_or_404(ExportBatch, pk=pk)
    try:
        services.supersede(batch, request.user, request.POST.get("reason"))
        messages.success(request, _("Superseded. The payroll is back to Approved and can be exported again."))
    except services.ExportError as exc:
        messages.error(request, str(exc))
    return redirect("bx-run", pk=batch.run_id)


@login_required
@permission_required(P + "mark_export_paid")
@require_POST
def paid(request, pk):
    batch = get_object_or_404(ExportBatch, pk=pk)
    try:
        services.mark_paid(batch, request.user)
        messages.success(request, _("Marked as paid."))
    except services.ExportError as exc:
        messages.error(request, str(exc))
    return redirect("bx-run", pk=batch.run_id)


@login_required
@permission_required(P + "view_bank")
def banks(request):
    form = BankUploadForm(request.POST or None, request.FILES or None)
    report = None
    if request.method == "POST" and request.user.has_perm(P + "add_bank") and form.is_valid():
        f = form.cleaned_data["file"]
        try:
            rows, errors = services.load_bank_rows(f, f.name)
            report = {**services.import_banks(rows), "errors": errors}
            messages.success(request, _("Bank list imported."))
        except services.ExportError as exc:
            messages.error(request, str(exc))
    return render(request, T + "banks.html", {
        "banks": Bank.objects.all(), "form": form, "report": report,
        "can_upload": request.user.has_perm(P + "add_bank"),
    })


@login_required
@permission_required(P + "view_bank_accounts")
def accounts(request):
    """Every active employee with what the employee record says and what the bank list matched."""
    q = request.GET.get("q", "").strip()
    only_problems = request.GET.get("problems") == "1"
    qs = Employee.objects.filter(is_active=True).select_related("employee_work_info", "employee_work_info__department_id")
    if q:
        qs = qs.filter(
            Q(badge_id__icontains=q) | Q(employee_first_name__icontains=q) | Q(employee_last_name__icontains=q)
        )
    dept = request.GET.get("department")
    if dept:
        qs = qs.filter(employee_work_info__department_id=dept)
    qs = qs.order_by("badge_id", "employee_first_name")
    rows = []
    for e in qs:
        bank, acct, issues = services.resolve_bank(e)
        if only_problems and not issues:
            continue
        name, code, raw_acct = services.record_text(e)
        rows.append({"employee": e, "bank": bank, "account": services.mask(acct), "issues": issues,
                     "record_bank": name or code, "has_record": bool(raw_acct or name or code)})
    page = Paginator(rows, 50).get_page(request.GET.get("page"))
    return render(request, T + "accounts.html", {
        "page": page, "q": q, "only_problems": only_problems, "dept": dept, "total": len(rows),
        "departments": Department.objects.all(),
        "can_edit": request.user.has_perm(P + "edit_bank_accounts"),
        "problem_count": sum(1 for r in rows if r["issues"]),
    })


@login_required
@permission_required(P + "edit_bank_accounts")
def account_edit(request, employee_id):
    emp = get_object_or_404(Employee, pk=employee_id)
    bank, acct, _issues = services.resolve_bank(emp)
    form = WorkerBankForm(request.POST or None, initial={"employee": emp, "bank": bank, "account_number": acct})
    form.fields["employee"].disabled = True
    if request.method == "POST" and form.is_valid():
        try:
            services.save_worker_bank(emp, form.cleaned_data["bank"], form.cleaned_data["account_number"])
        except services.ExportError as exc:
            form.add_error("account_number", str(exc))
        else:
            messages.success(request, _("Bank account saved."))
            return redirect("bx-accounts")
    return render(request, T + "account_form.html", {"form": form, "emp": emp})
