"""
Vendor-neutral biometric integration models.

``RawTransaction`` is the audit record of what the source system reported. It
is append-only by convention: only the processing fields (employee,
processing_status, error) are ever updated. No fingerprint or face data is
stored, only attendance metadata.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from employee.models import Employee
from horilla.models import HorillaModel

PROVIDER_MANUAL_FILE = "manual_file"
PROVIDER_DIRECT_ZK = "direct_zkteco"
PROVIDER_BIOTIME = "biotime"
PROVIDER_TIMENET = "timenet"


class ProviderConfig(HorillaModel):
    """One configured source of attendance transactions."""

    PROVIDER_CHOICES = [
        (PROVIDER_MANUAL_FILE, _("Manual CSV/Excel upload")),
        (PROVIDER_DIRECT_ZK, _("Direct ZKTeco device")),
        (PROVIDER_BIOTIME, _("ZKTeco BioTime")),
        (PROVIDER_TIMENET, _("ZKTime.Net")),
    ]

    name = models.CharField(max_length=100, unique=True, verbose_name=_("Name"))
    provider_type = models.CharField(
        max_length=30, choices=PROVIDER_CHOICES, verbose_name=_("Provider")
    )
    source_timezone = models.CharField(
        max_length=64,
        default="Africa/Lagos",
        verbose_name=_("Source timezone"),
        help_text=_("Timezone of timestamps that arrive without one."),
    )
    device = models.ForeignKey(
        "biometric.BiometricDevices",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        verbose_name=_("Horilla device"),
        help_text=_("Direct ZKTeco only: reuses the device already registered."),
    )
    config = models.JSONField(
        default=dict,
        blank=True,
        verbose_name=_("Settings"),
        help_text=_(
            "Non-secret settings only (e.g. column overrides). Never store "
            "passwords or API keys here."
        ),
    )

    class Meta:
        verbose_name = _("Biometric provider")
        verbose_name_plural = _("Biometric providers")

    def __str__(self):
        return self.name


class ExternalEmployeeMapping(HorillaModel):
    """Links an identifier used by a provider to a Horilla employee."""

    VERIFIED = "verified"
    SUGGESTED = "suggested"
    STATUS_CHOICES = [(VERIFIED, _("Verified")), (SUGGESTED, _("Suggested"))]

    provider = models.ForeignKey(
        ProviderConfig, on_delete=models.CASCADE, related_name="mappings"
    )
    employee = models.ForeignKey(
        Employee,
        on_delete=models.CASCADE,
        related_name="biometric_mappings",
        verbose_name=_("Employee"),
    )
    external_employee_id = models.CharField(
        max_length=100, verbose_name=_("Biometric user ID")
    )
    external_employee_code = models.CharField(max_length=100, blank=True, default="")
    external_device_user_id = models.CharField(max_length=100, blank=True, default="")
    status = models.CharField(
        max_length=12, choices=STATUS_CHOICES, default=VERIFIED, db_index=True
    )
    last_synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = _("Biometric employee mapping")
        verbose_name_plural = _("Biometric employee mappings")
        constraints = [
            # One live identifier -> one employee, per provider.
            models.UniqueConstraint(
                fields=["provider", "external_employee_id"],
                condition=models.Q(is_active=True),
                name="uniq_active_external_id_per_provider",
            ),
        ]

    def __str__(self):
        return f"{self.provider} {self.external_employee_id} -> {self.employee}"


class ImportBatch(models.Model):
    """One import run (file upload or connector sync) and its outcome."""

    RUNNING, SUCCESS, PARTIAL, FAILED = "running", "success", "partial", "failed"
    STATUS_CHOICES = [
        (RUNNING, _("Running")),
        (SUCCESS, _("Success")),
        (PARTIAL, _("Completed with errors")),
        (FAILED, _("Failed")),
    ]
    MANUAL, SCHEDULED = "manual", "scheduled"

    provider = models.ForeignKey(
        ProviderConfig, on_delete=models.PROTECT, related_name="batches"
    )
    trigger = models.CharField(max_length=12, default=MANUAL)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=RUNNING)
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(null=True, blank=True)
    file_name = models.CharField(max_length=255, blank=True, default="")
    file_sha256 = models.CharField(max_length=64, blank=True, default="")
    received_count = models.PositiveIntegerField(default=0)
    imported_count = models.PositiveIntegerField(default=0)
    duplicate_count = models.PositiveIntegerField(default=0)
    unmapped_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)
    errors = models.JSONField(default=list, blank=True)
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )

    class Meta:
        ordering = ["-started_at", "-id"]
        verbose_name = _("Import batch")
        verbose_name_plural = _("Import batches")

    def __str__(self):
        return f"Batch {self.pk} ({self.provider})"


class RawTransaction(models.Model):
    """A normalised attendance transaction exactly as the source reported it."""

    PENDING, MAPPED, UNMAPPED, PROCESSED, ERROR = (
        "pending",
        "mapped",
        "unmapped",
        "processed",
        "error",
    )
    STATUS_CHOICES = [
        (PENDING, _("Pending")),
        (MAPPED, _("Mapped")),
        (UNMAPPED, _("Unmapped")),
        (PROCESSED, _("Processed")),
        (ERROR, _("Error")),
    ]

    provider = models.ForeignKey(
        ProviderConfig, on_delete=models.PROTECT, related_name="transactions"
    )
    batch = models.ForeignKey(
        ImportBatch, on_delete=models.PROTECT, related_name="transactions"
    )
    dedup_key = models.CharField(max_length=64, unique=True)
    external_transaction_id = models.CharField(max_length=100, blank=True, default="")
    external_employee_id = models.CharField(max_length=100, db_index=True)
    employee = models.ForeignKey(
        Employee,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="biometric_transactions",
    )
    external_device_id = models.CharField(max_length=100, blank=True, default="")
    attendance_timestamp = models.DateTimeField(db_index=True)
    source_timezone = models.CharField(max_length=64, blank=True, default="")
    verification_method = models.CharField(max_length=30, blank=True, default="")
    original_data = models.JSONField(default=dict, blank=True)
    imported_at = models.DateTimeField(default=timezone.now)
    processing_status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default=PENDING, db_index=True
    )
    error = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        ordering = ["attendance_timestamp", "id"]
        indexes = [models.Index(fields=["employee", "attendance_timestamp"])]
        verbose_name = _("Raw biometric transaction")
        verbose_name_plural = _("Raw biometric transactions")

    def __str__(self):
        return f"{self.external_employee_id} @ {self.attendance_timestamp:%Y-%m-%d %H:%M}"
