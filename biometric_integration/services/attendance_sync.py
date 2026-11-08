"""
Runs an import: normalise, de-duplicate, map to employees, store raw rows, and
record the batch. Idempotent: the same transaction can be imported any number of
times and yields exactly one RawTransaction.
"""

import hashlib

from django.db import transaction
from django.utils import timezone

from ..models import ImportBatch, RawTransaction
from . import duplicate_detection, employee_mapping, transaction_normalizer

CHUNK = 1000


def file_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def import_transactions(provider, transactions, row_errors=(), *, trigger=ImportBatch.MANUAL,
                        user=None, file_name="", file_hash=""):
    batch = ImportBatch.objects.create(
        provider=provider,
        trigger=trigger,
        triggered_by=user if getattr(user, "pk", None) else None,
        file_name=file_name,
        file_sha256=file_hash,
    )
    try:
        _run(batch, provider, list(transactions), list(row_errors))
    except Exception as exc:  # leave a trace instead of a stuck "running" batch
        batch.status = ImportBatch.FAILED
        batch.errors = [*batch.errors, {"row": None, "message": f"Import aborted: {exc}"}]
        batch.finished_at = timezone.now()
        batch.save()
        raise
    return batch


def fail_batch(provider, message, *, trigger=ImportBatch.MANUAL, user=None, file_name="",
               file_hash=""):
    """Record a batch that never got as far as parsing rows (bad file, device down)."""
    return ImportBatch.objects.create(
        provider=provider,
        trigger=trigger,
        triggered_by=user if getattr(user, "pk", None) else None,
        status=ImportBatch.FAILED,
        file_name=file_name,
        file_sha256=file_hash,
        errors=[{"row": None, "message": message}],
        finished_at=timezone.now(),
    )


def _run(batch, provider, transactions, row_errors):
    batch.received_count = len(transactions) + len(row_errors)
    good, norm_errors = transaction_normalizer.normalize(
        transactions, provider.source_timezone
    )
    all_errors = [e.as_dict() for e in row_errors] + [e.as_dict() for e in norm_errors]

    index = employee_mapping.active_mapping_index(provider)
    keyed = {}
    in_file_dupes = 0
    for txn, aware in good:
        key = duplicate_detection.dedup_key(
            provider.pk, txn.external_employee_id, aware,
            txn.external_device_id, txn.external_transaction_id,
        )
        if key in keyed:
            in_file_dupes += 1
            continue
        keyed[key] = (txn, aware)

    existing = set()
    keys = list(keyed)
    for i in range(0, len(keys), CHUNK):
        existing.update(
            RawTransaction.objects.filter(dedup_key__in=keys[i : i + CHUNK]).values_list(
                "dedup_key", flat=True
            )
        )

    new_rows, unmapped = [], 0
    for key, (txn, aware) in keyed.items():
        if key in existing:
            continue
        mapping = index.get(txn.external_employee_id)
        if mapping is None:
            unmapped += 1
        new_rows.append(
            RawTransaction(
                provider=provider,
                batch=batch,
                dedup_key=key,
                external_transaction_id=txn.external_transaction_id,
                external_employee_id=txn.external_employee_id,
                employee=mapping.employee if mapping else None,
                external_device_id=txn.external_device_id,
                attendance_timestamp=aware,
                source_timezone=provider.source_timezone,
                verification_method=txn.verification_method,
                original_data=txn.raw,
                processing_status=RawTransaction.MAPPED if mapping else RawTransaction.UNMAPPED,
            )
        )

    with transaction.atomic():
        for i in range(0, len(new_rows), CHUNK):
            # ignore_conflicts: a concurrent import of the same rows is a no-op.
            RawTransaction.objects.bulk_create(new_rows[i : i + CHUNK], ignore_conflicts=True)

    stamps = [a for _, a in good]
    batch.period_start = min(stamps).date() if stamps else None
    batch.period_end = max(stamps).date() if stamps else None
    batch.imported_count = len(new_rows)
    batch.duplicate_count = len(existing) + in_file_dupes
    batch.unmapped_count = unmapped
    batch.failed_count = len(all_errors)
    batch.errors = all_errors[:500]
    batch.status = ImportBatch.PARTIAL if all_errors else ImportBatch.SUCCESS
    batch.finished_at = timezone.now()
    batch.save()
    employee_mapping.mark_synced(index.values())
