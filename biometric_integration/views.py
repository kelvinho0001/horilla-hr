import hashlib

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Max, OuterRef, Q, Exists
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from horilla.decorators import login_required, permission_required

from .connectors.base import ConnectorError
from .connectors.registry import get_connector
from .forms import MappingForm, UploadForm, WorkerSheetForm
from .models import (
    PROVIDER_MANUAL_FILE,
    ExternalEmployeeMapping,
    IgnoredExternalID,
    ImportBatch,
    ProviderConfig,
    RawTransaction,
)
from .connectors.manual_file import device_serial_from_filename
from .services import attendance_sync, employee_mapping, worker_sheet

T = "biometric_integration/"


def _default_manual_provider():
    provider, _created = ProviderConfig.objects.get_or_create(
        name="Manual upload",
        defaults={"provider_type": PROVIDER_MANUAL_FILE},
    )
    return provider


@login_required
@permission_required("biometric_integration.view_importbatch")
def dashboard(request):
    batches = ImportBatch.objects.select_related("provider")
    agg = ImportBatch.objects.aggregate(
        last_attempt=Max("started_at"),
        last_ok=Max("started_at", filter=Q(status__in=["success", "partial"])),
    )
    sums = {
        k: sum(batches.values_list(k, flat=True))
        for k in ("duplicate_count", "failed_count")
    }
    unmapped_ids = (
        RawTransaction.objects.filter(employee__isnull=True)
        .exclude(Exists(IgnoredExternalID.objects.filter(
            provider_id=OuterRef("provider_id"), external_employee_id=OuterRef("external_employee_id"))))
        .values("external_employee_id")
        .distinct()
        .count()
    )
    return render(
        request,
        T + "dashboard.html",
        {
            "providers": ProviderConfig.objects.filter(is_active=True),
            "transactions_total": RawTransaction.objects.count(),
            "duplicates_total": sums["duplicate_count"],
            "failed_total": sums["failed_count"],
            "unmapped_ids": unmapped_ids,
            "last_attempt": agg["last_attempt"],
            "last_ok": agg["last_ok"],
            "recent": batches[:10],
        },
    )


@login_required
@permission_required("biometric_integration.add_importbatch")
def upload(request):
    form = UploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        f = form.cleaned_data["file"]
        provider = form.cleaned_data["provider"]
        if provider is None:
            serial = device_serial_from_filename(f.name)
            if serial:
                # One source per device: IDs are only unique within a device, so punches from different devices never mix.
                provider, _c = ProviderConfig.objects.get_or_create(
                    name=f"Device {serial}", defaults={"provider_type": PROVIDER_MANUAL_FILE}
                )
            else:
                provider = _default_manual_provider()
        data = f.read()
        digest = hashlib.sha256(data).hexdigest()
        import io

        connector = get_connector(provider)
        try:
            txns, errors = connector.parse(io.BytesIO(data), f.name)
        except ConnectorError as exc:
            batch = attendance_sync.fail_batch(
                provider, str(exc), user=request.user, file_name=f.name, file_hash=digest
            )
            messages.error(request, str(exc))
            return redirect("bio-int-batch-detail", pk=batch.pk)
        batch = attendance_sync.import_transactions(
            provider, txns, errors, user=request.user, file_name=f.name, file_hash=digest
        )
        if ImportBatch.objects.filter(file_sha256=digest).exclude(pk=batch.pk).exists():
            messages.warning(
                request, _("This exact file was uploaded before; duplicates were skipped.")
            )
        messages.success(
            request,
            _("%(p)s: %(n)s new transactions imported, %(d)s duplicates skipped.")
            % {"p": provider.name, "n": batch.imported_count, "d": batch.duplicate_count},
        )
        return redirect("bio-int-batch-detail", pk=batch.pk)
    return render(request, T + "upload.html", {"form": form})


@login_required
@permission_required("biometric_integration.view_importbatch")
def batch_list(request):
    page = Paginator(ImportBatch.objects.select_related("provider"), 25).get_page(
        request.GET.get("page")
    )
    return render(request, T + "batch_list.html", {"page": page})


@login_required
@permission_required("biometric_integration.view_importbatch")
def batch_detail(request, pk):
    batch = get_object_or_404(ImportBatch.objects.select_related("provider"), pk=pk)
    return render(request, T + "batch_detail.html", {"batch": batch})


@login_required
@permission_required("biometric_integration.view_externalemployeemapping")
def mapping_list(request):
    qs = ExternalEmployeeMapping.objects.select_related("employee", "provider").order_by(
        "provider__name", "external_employee_id"
    )
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(
            Q(external_employee_id__icontains=q)
            | Q(employee__employee_first_name__icontains=q)
            | Q(employee__employee_last_name__icontains=q)
            | Q(employee__badge_id__icontains=q)
        )
    page = Paginator(qs, 25).get_page(request.GET.get("page"))
    return render(request, T + "mapping_list.html", {"page": page, "q": q})


@login_required
@permission_required("biometric_integration.add_externalemployeemapping")
def mapping_create(request):
    initial = {k: request.GET[k] for k in ("provider", "external_employee_id") if k in request.GET}
    if "employee" in request.GET:
        initial["employee"] = request.GET["employee"]
    form = MappingForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            employee_mapping.map_employee(
                cd["provider"], cd["employee"], cd["external_employee_id"], status=cd["status"]
            )
        except employee_mapping.MappingConflict as exc:
            form.add_error("external_employee_id", str(exc))
        else:
            messages.success(request, _("Mapping saved."))
            return redirect("bio-int-mapping-list")
    return render(request, T + "mapping_form.html", {"form": form})


@login_required
@permission_required("biometric_integration.change_externalemployeemapping")
@require_POST
def mapping_deactivate(request, pk):
    mapping = get_object_or_404(ExternalEmployeeMapping, pk=pk)
    employee_mapping.deactivate_mapping(mapping)
    messages.success(request, _("Mapping deactivated."))
    return redirect("bio-int-mapping-list")


@login_required
@permission_required("biometric_integration.view_externalemployeemapping")
def unmapped_list(request):
    sections = []
    for p in ProviderConfig.objects.filter(is_active=True):
        rows = employee_mapping.unmapped_summary(p)
        sections.append({
            "provider": p, "rows": rows,
            "suggested": sum(1 for r in rows if r["suggestion"]),
            "ignored": IgnoredExternalID.objects.filter(provider=p),
        })
    return render(request, T + "unmapped_list.html", {
        "sections": sections, "can_map": request.user.has_perm("biometric_integration.add_externalemployeemapping"),
    })


@login_required
@permission_required("biometric_integration.add_externalemployeemapping")
@require_POST
def map_all(request, provider_id):
    provider = get_object_or_404(ProviderConfig, pk=provider_id)
    mapped, skipped = employee_mapping.map_all_suggested(provider)
    messages.success(request, _("%(m)s IDs mapped to the worker with the same Badge ID; %(s)s skipped.") % {"m": mapped, "s": skipped})
    return redirect("bio-int-unmapped")


@login_required
@permission_required("biometric_integration.add_externalemployeemapping")
@require_POST
def ignore_id(request, provider_id):
    provider = get_object_or_404(ProviderConfig, pk=provider_id)
    try:
        employee_mapping.ignore_id(provider, request.POST.get("external_employee_id", ""), request.POST.get("reason"), request.user)
        messages.success(request, _("ID ignored. Its punches are kept but it is no longer listed."))
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect("bio-int-unmapped")


@login_required
@permission_required("biometric_integration.add_externalemployeemapping")
@require_POST
def unignore_id(request, provider_id):
    provider = get_object_or_404(ProviderConfig, pk=provider_id)
    employee_mapping.unignore_id(provider, request.POST.get("external_employee_id", ""))
    return redirect("bio-int-unmapped")


@login_required
@permission_required("biometric_integration.add_externalemployeemapping")
def worker_sheet_view(request):
    form = WorkerSheetForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            users = worker_sheet.parse_user_dat(cd["user_file"].read())
            timenet = None
            if cd["timenet_file"]:
                import io

                timenet = worker_sheet.read_timenet_export(io.BytesIO(cd["timenet_file"].read()), cd["timenet_file"].name)
            exclude = [x for x in cd["exclude_ids"].replace("\n", ",").split(",")]
            rows, excluded, unknown, tn_only = worker_sheet.build_rows(users, timenet, exclude, cd["provider"])
            if not rows:
                raise worker_sheet.WorkerSheetError("Every ID on the device is already in Horilla or was left out: nothing to create.")
            content = worker_sheet.build_workbook(
                rows, excluded, unknown, tn_only, company=cd["company"].company, email_domain=cd["email_domain"],
                defaults={k: cd[k] for k in ("job_position", "job_role", "shift", "work_type", "employee_type", "location", "date_joining")},
            )
        except (worker_sheet.WorkerSheetError, ConnectorError) as exc:
            form.add_error(None, str(exc))
        else:
            from django.http import HttpResponse

            resp = HttpResponse(content, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            resp["Content-Disposition"] = 'attachment; filename="workers-to-import.xlsx"'
            return resp
    return render(request, T + "worker_sheet.html", {"form": form})
