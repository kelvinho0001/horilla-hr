import hashlib

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Max, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from horilla.decorators import login_required, permission_required

from .connectors.base import ConnectorError
from .connectors.registry import get_connector
from .forms import MappingForm, UploadForm
from .models import (
    PROVIDER_MANUAL_FILE,
    ExternalEmployeeMapping,
    ImportBatch,
    ProviderConfig,
    RawTransaction,
)
from .services import attendance_sync, employee_mapping

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
        provider = form.cleaned_data["provider"] or _default_manual_provider()
        f = form.cleaned_data["file"]
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
            _("%(n)s new transactions imported, %(d)s duplicates skipped.")
            % {"n": batch.imported_count, "d": batch.duplicate_count},
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
    sections = [
        {"provider": p, "rows": employee_mapping.unmapped_summary(p)}
        for p in ProviderConfig.objects.filter(is_active=True)
    ]
    return render(request, T + "unmapped_list.html", {"sections": sections})
