"""Payroll calculation, loan ledger, fines and the run lifecycle."""

import calendar
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from attendance_processing import calculators
from attendance_processing.models import PRESENT, DailyAttendance
from employee.models import Employee

from .models import (
    Fine, LoanAgreement, LoanRepayment, PayrollLine, PayrollRun, WorkerPayProfile,
)
from .money import D, kobo


class PayrollError(Exception):
    pass


# ---------------------------------------------------------------- formula
def amount_earned(total_days, working_days, basic, fines, iou):
    """
    The sheet's formula, with the direction confirmed by Demiller:
    days attended / working days x basic - fines - IOU, to the kobo.
    """
    if not working_days:
        raise PayrollError("Working days must be greater than zero.")
    earned = kobo(D(total_days) * D(basic) / D(working_days))
    return earned - D(fines) - D(iou)


# ---------------------------------------------------------------- loans
def _add_months(d, n):
    m = d.month - 1 + n
    y, mo = d.year + m // 12, m % 12 + 1
    return date(y, mo, min(d.day, calendar.monthrange(y, mo)[1]))


def due_date(agreement, k):
    """Due date of instalment k (1-based)."""
    start = agreement.first_due_date
    if agreement.frequency == LoanAgreement.WEEKLY:
        return start + timedelta(weeks=k - 1)
    # anchor on the original day so a 31st clamps per month without drifting
    return _add_months(start, k - 1)


def due_instalments(agreement, through):
    """
    Uncollected instalments due on or before ``through``, capped so the total
    never exceeds the outstanding balance. Returns [(k, due_date, amount)].
    """
    if agreement.status != LoanAgreement.APPROVED:
        return []
    collected = set(
        agreement.repayments.filter(installment_no__isnull=False).values_list("installment_no", flat=True)
    )
    remaining = agreement.outstanding()
    out, k = [], 1
    while remaining > 0:
        dd = due_date(agreement, k)
        if dd > through:
            break
        if k not in collected:
            amt = min(agreement.instalment_amount, remaining)
            out.append((k, dd, amt))
            remaining -= amt
        k += 1
        if k > 1000:  # safety net against bad data
            break
    return out


def approve_agreement(agreement, user):
    if agreement.status != LoanAgreement.PENDING:
        raise PayrollError("Only a pending agreement can be approved.")
    agreement.principal = kobo(agreement.loan.loan_amount)
    if agreement.principal <= 0:
        raise PayrollError("The loan amount must be greater than zero.")
    agreement.status = LoanAgreement.APPROVED
    agreement.approved_by, agreement.approved_at = user, timezone.now()
    agreement.save()
    return agreement


@transaction.atomic
def record_adjustment(agreement, kind, amount, note, user):
    """Early settlement or authorised adjustment; reduces the balance."""
    amount = kobo(amount)
    if kind not in (LoanRepayment.SETTLEMENT, LoanRepayment.ADJUSTMENT):
        raise PayrollError("Invalid repayment kind.")
    if not (note or "").strip():
        raise PayrollError("A note is required.")
    agreement = LoanAgreement.objects.select_for_update().get(pk=agreement.pk)
    if agreement.status != LoanAgreement.APPROVED:
        raise PayrollError("The agreement is not active.")
    if amount <= 0 or amount > agreement.outstanding():
        raise PayrollError("Amount must be positive and not exceed the outstanding balance.")
    LoanRepayment.objects.create(agreement=agreement, kind=kind, amount=amount, note=note, user=user)
    _close_if_cleared(agreement)
    return agreement


def _close_if_cleared(agreement):
    if agreement.outstanding() <= 0:
        agreement.status = LoanAgreement.SETTLED
        agreement.save()
        loan = agreement.loan
        loan.settled = True
        loan.save()


# ---------------------------------------------------------------- pay profiles
def profile_for(employee_id, on_date):
    return (
        WorkerPayProfile.objects.filter(
            employee_id=employee_id, status=WorkerPayProfile.APPROVED, effective_from__lte=on_date
        )
        .order_by("-effective_from")
        .first()
    )


# ---------------------------------------------------------------- generation
def _editable(run):
    if run.is_final:
        raise PayrollError("This payroll is approved and cannot be recalculated or edited.")


@transaction.atomic
def generate(run):
    """
    Build or rebuild the lines of a draft/reviewed run from APPROVED attendance.
    HR's per-worker working-days overrides survive a rebuild. Nothing is written
    to the loan ledger or the fines here.
    """
    run = PayrollRun.objects.select_for_update().get(pk=run.pk)
    _editable(run)
    weeks = calculators.week_buckets(run.start_date, run.end_date)

    profiles = {}
    emps = Employee.objects.filter(is_active=True).select_related("employee_work_info", "employee_work_info__department_id")
    if run.department_id:
        emps = emps.filter(employee_work_info__department_id=run.department_id)
    for e in emps:
        p = profile_for(e.pk, run.end_date)
        if p:
            profiles[e.pk] = (e, p)
    ids = list(profiles)

    present, unapproved = defaultdict(set), defaultdict(int)
    for d in DailyAttendance.objects.filter(
        employee_id__in=ids, date__range=(run.start_date, run.end_date), status=PRESENT
    ):
        if d.approval_id:
            present[d.employee_id].add(d.date)
        else:
            unapproved[d.employee_id] += 1

    fines = defaultdict(list)
    for f in Fine.objects.filter(
        employee_id__in=ids, status=Fine.APPROVED, applied_run__isnull=True,
        date_issued__lte=run.end_date,
    ):
        fines[f.employee_id].append(f)

    agreements = defaultdict(list)
    for a in LoanAgreement.objects.filter(loan__employee_id__in=ids, status=LoanAgreement.APPROVED).select_related("loan"):
        agreements[a.loan.employee_id_id].append(a)

    existing = {l.employee_id: l for l in run.lines.all()}
    for emp_id in set(existing) - set(ids):
        existing[emp_id].delete()

    total_net = Decimal("0")
    for emp_id in ids:
        e, p = profiles[emp_id]
        days = present.get(emp_id, set())
        week_counts = [sum(1 for d in days if a <= d <= b) for a, b in weeks]
        line = existing.get(emp_id) or PayrollLine(run=run, employee=e)
        override = line.working_days_override
        working = override if override is not None else run.default_working_days

        fine_items = [{"id": f.pk, "description": f.description, "amount": str(f.amount)} for f in fines.get(emp_id, [])]
        fine_total = sum((f.amount for f in fines.get(emp_id, [])), Decimal("0"))
        loan_items, iou = [], Decimal("0")
        for a in agreements.get(emp_id, []):
            for k, dd, amt in due_instalments(a, run.end_date):
                loan_items.append({"agreement": a.pk, "loan": a.loan.title, "no": k, "due": dd.isoformat(), "amount": str(amt)})
                iou += amt

        flags = []
        if unapproved.get(emp_id):
            flags.append(f"{unapproved[emp_id]} present day(s) not yet approved - not counted")
        if not days:
            flags.append("No approved attendance in range")
        if working and sum(week_counts) > working:
            flags.append("Days attended exceed working days")
        if not working:
            flags.append("Working days not set")
            earned = Decimal("0")
        else:
            earned = amount_earned(sum(week_counts), working, p.basic_salary, fine_total, iou)
            if earned < 0:
                flags.append("Net pay is negative")

        wi = getattr(e, "employee_work_info", None)
        line.emp_code = e.badge_id or ""
        line.first_name, line.last_name = e.employee_first_name or "", e.employee_last_name or ""
        line.department = str(wi.department_id) if wi and wi.department_id_id else ""
        line.week_days, line.total_days = week_counts, sum(week_counts)
        line.working_days = working
        line.basic_salary, line.fines, line.iou, line.amount_earned = p.basic_salary, fine_total, iou, earned
        line.flags = flags
        line.breakdown = {
            "formula": f"{sum(week_counts)} / {working} x {p.basic_salary} - {fine_total} - {iou}",
            "fines": fine_items, "loans": loan_items, "profile_id": p.pk,
        }
        line.save()
        total_net += earned

    run.status = PayrollRun.DRAFT
    run.reviewed_by = run.reviewed_at = None
    run.total_net, run.employee_count = total_net, len(ids)
    run.save()
    return run


@transaction.atomic
def set_working_days(line, days, user=None):
    run = PayrollRun.objects.select_for_update().get(pk=line.run_id)
    _editable(run)
    if days is None or int(days) <= 0:
        raise PayrollError("Working days must be greater than zero.")
    line = PayrollLine.objects.get(pk=line.pk)
    line.working_days_override = line.working_days = int(days)
    line.amount_earned = amount_earned(line.total_days, line.working_days, line.basic_salary, line.fines, line.iou)
    line.flags = [f for f in line.flags if f not in ("Working days not set", "Days attended exceed working days", "Net pay is negative")]
    if line.total_days > line.working_days:
        line.flags.append("Days attended exceed working days")
    if line.amount_earned < 0:
        line.flags.append("Net pay is negative")
    line.save()
    _refresh_totals(run)
    if run.status == PayrollRun.REVIEWED:
        run.status, run.reviewed_by, run.reviewed_at = PayrollRun.DRAFT, None, None
        run.save()
    return line


def _refresh_totals(run):
    lines = run.lines.all()
    run.total_net = sum((l.amount_earned for l in lines), Decimal("0"))
    run.employee_count = len(lines)
    run.save()


# ---------------------------------------------------------------- lifecycle
def blockers(run):
    out = []
    lines = list(run.lines.all())
    if not lines:
        out.append("The run has no workers. Generate it first.")
    for l in lines:
        if not l.working_days:
            out.append(f"{l.emp_code} {l.first_name}: working days not set")
    return out


@transaction.atomic
def mark_reviewed(run, user):
    run = PayrollRun.objects.select_for_update().get(pk=run.pk)
    _editable(run)
    if run.status != PayrollRun.DRAFT:
        raise PayrollError("Only a draft can be marked reviewed.")
    problems = blockers(run)
    if problems:
        raise PayrollError("; ".join(problems[:5]))
    run.status, run.reviewed_by, run.reviewed_at = PayrollRun.REVIEWED, user, timezone.now()
    run.save()
    return run


@transaction.atomic
def approve(run, user):
    """
    Approve: re-checks that every loan instalment and fine is still unused, then
    writes the ledger and locks the run. Anything stale means recalculate.
    """
    run = PayrollRun.objects.select_for_update().get(pk=run.pk)
    if run.status != PayrollRun.REVIEWED:
        raise PayrollError("Only a reviewed payroll can be approved.")
    problems = blockers(run)
    if problems:
        raise PayrollError("; ".join(problems[:5]))

    clash = (
        PayrollLine.objects.filter(
            run__status=PayrollRun.APPROVED, run__start_date__lte=run.end_date,
            run__end_date__gte=run.start_date, employee__in=run.lines.values("employee"),
        )
        .exclude(run=run)
        .select_related("employee", "run")
        .first()
    )
    if clash:
        raise PayrollError(
            f"{clash.first_name} {clash.last_name} is already paid in approved run '{clash.run}' "
            "for an overlapping period."
        )

    try:
        with transaction.atomic():
            for line in run.lines.select_related("employee"):
                for item in line.breakdown.get("loans", []):
                    agreement = LoanAgreement.objects.select_for_update().get(pk=item["agreement"])
                    amount = D(item["amount"])
                    if agreement.status != LoanAgreement.APPROVED or amount > agreement.outstanding():
                        raise PayrollError("A loan balance changed since this run was calculated. Recalculate.")
                    LoanRepayment.objects.create(
                        agreement=agreement, kind=LoanRepayment.INSTALMENT, amount=amount,
                        installment_no=item["no"], due_date=date.fromisoformat(item["due"]),
                        run=run, user=user, note=f"Payroll {run.name}",
                    )
                    _close_if_cleared(agreement)
                for item in line.breakdown.get("fines", []):
                    n = Fine.objects.filter(
                        pk=item["id"], status=Fine.APPROVED, applied_run__isnull=True
                    ).update(applied_run=run)
                    if n != 1:
                        raise PayrollError("A fine changed since this run was calculated. Recalculate.")
    except IntegrityError as exc:
        raise PayrollError(
            "A loan instalment in this run was already collected by another run. Recalculate."
        ) from exc

    run.status, run.approved_by, run.approved_at = PayrollRun.APPROVED, user, timezone.now()
    run.save()
    return run


# ---------------------------------------------------------------- fines
def approve_fine(fine, user):
    if fine.status != Fine.PENDING:
        raise PayrollError("Only a pending fine can be approved.")
    fine.status, fine.approved_by, fine.approved_at = Fine.APPROVED, user, timezone.now()
    fine.save()
    return fine


def waive_fine(fine, user, reason):
    if not (reason or "").strip():
        raise PayrollError("A reason is required to waive a fine.")
    if fine.applied_run_id:
        raise PayrollError("This fine has already been deducted in an approved payroll.")
    fine.status, fine.waived_by, fine.waived_at, fine.waive_reason = (
        Fine.WAIVED, user, timezone.now(), reason.strip(),
    )
    fine.save()
    return fine
