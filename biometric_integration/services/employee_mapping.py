"""
Mapping external biometric identifiers to Horilla employees.

Rules: an active identifier maps to exactly one employee per provider; names are
never used to decide a match; ambiguous suggestions are left for an
administrator.
"""

from django.db import IntegrityError, transaction
from django.db.models import Count
from django.utils import timezone

from employee.models import Employee

from ..models import ExternalEmployeeMapping, RawTransaction


class MappingConflict(Exception):
    pass


def active_mapping_index(provider):
    return {
        m.external_employee_id: m
        for m in ExternalEmployeeMapping.objects.filter(provider=provider, is_active=True)
    }


def suggest_employee(external_id):
    """Exact badge_id match only. Returns an Employee or None (ambiguous or no match)."""
    matches = list(Employee.objects.filter(badge_id=external_id)[:2])
    return matches[0] if len(matches) == 1 else None


@transaction.atomic
def map_employee(provider, employee, external_employee_id, status=ExternalEmployeeMapping.VERIFIED,
                 code="", device_user_id=""):
    """Create (or confirm) a mapping, then link past unmapped transactions."""
    external_employee_id = str(external_employee_id).strip()
    existing = ExternalEmployeeMapping.objects.filter(
        provider=provider, external_employee_id=external_employee_id, is_active=True
    ).first()
    if existing:
        if existing.employee_id != employee.pk:
            raise MappingConflict(
                f"ID {external_employee_id} is already mapped to {existing.employee}."
            )
        if existing.status != status:
            existing.status = status
            existing.save()
        mapping = existing
    else:
        try:
            with transaction.atomic():
                mapping = ExternalEmployeeMapping.objects.create(
                    provider=provider,
                    employee=employee,
                    external_employee_id=external_employee_id,
                    external_employee_code=code,
                    external_device_user_id=device_user_id,
                    status=status,
                )
        except IntegrityError as exc:
            raise MappingConflict(
                f"ID {external_employee_id} is already mapped."
            ) from exc
    if mapping.status == ExternalEmployeeMapping.VERIFIED:
        relink_transactions(mapping)
    return mapping


def relink_transactions(mapping):
    """Attach earlier unmapped transactions of this identifier to the employee."""
    return RawTransaction.objects.filter(
        provider=mapping.provider,
        external_employee_id=mapping.external_employee_id,
        employee__isnull=True,
    ).update(employee=mapping.employee, processing_status=RawTransaction.MAPPED, error="")


@transaction.atomic
def deactivate_mapping(mapping):
    mapping.is_active = False
    mapping.save()


def unmapped_summary(provider):
    """Distinct identifiers with transactions but no employee, with suggestions."""
    rows = (
        RawTransaction.objects.filter(provider=provider, employee__isnull=True)
        .values("external_employee_id")
        .annotate(count=Count("id"))
        .order_by("external_employee_id")
    )
    out = []
    for r in rows:
        out.append(
            {
                "external_employee_id": r["external_employee_id"],
                "count": r["count"],
                "suggestion": suggest_employee(r["external_employee_id"]),
            }
        )
    return out


def mark_synced(mappings):
    ExternalEmployeeMapping.objects.filter(pk__in=[m.pk for m in mappings]).update(
        last_synced_at=timezone.now()
    )
