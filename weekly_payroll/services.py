"""Payroll calculation, the loan/fine ledger and the run lifecycle."""

from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from attendance.models import Attendance
from attendance_processing import calculators
from base.methods import get_nth_month_same_date
from biometric_integration.models import ExternalEmployeeMapping
from employee.models import Employee
from payroll.models.models import LoanAccount

from .models import LoanDeduction, PayrollLine, PayrollRun
from .money import D, kobo


class PayrollError(Exception):
    pass


# ---------------------------------------------------------------- formula
def amount_earned(total_days, working_days, basic, fines, iou):
    """
    The sheet's formula: days attended / working days x basic - fines - IOU, to the kobo.
    """
    if not working_days:
        raise PayrollError("Working days must be greater than zero.")
    earned = kobo(D(total_days) * D(basic) / D(working_days))
    return earned - D(fines) - D(iou)


# ---------------------------------------------------------------- Horilla loans, advances and fines
def installment_schedule(loan):
    """
    [(k, due_date, amount)] for a Horilla LoanAccount, exactly as Horilla schedules it: equal monthly
    instalments from the start date. The last one absorbs any kobo left over so the total is the loan amount.
    """
    n = int(loan.installments or 0)
    total = kobo(loan.loan_amount or 0)
    if n <= 0 or total <= 0:
        return []
    each = kobo(total / n)
    out = []
    for k in range(1, n + 1):
        amount = each if k < n else total - each * (n - 1)
        out.append((k, get_nth_month_same_date(loan.installment_start_date, k - 1), amount))
    return out


def due_items(loan, through, collected):
    """Uncollected instalments of this loan due on or before ``through``."""
    if loan.settled:
        return []
    return [(k, due, amt) for k, due, amt in installment_schedule(loan) if due <= through and k not in collected]


def is_fine(loan):
    return loan.type == "fine"


# ---------------------------------------------------------------- who is in a run
def payroll_employees(run):
    """
    Active workers with a verified biometric mapping or any validated attendance in the range,
    optionally limited to a department and/or worker category.
    """
    mapped = ExternalEmployeeMapping.objects.filter(
        is_active=True, status=ExternalEmployeeMapping.VERIFIED
    ).values_list("employee_id", flat=True)
    attended = Attendance.objects.filter(
        attendance_date__range=(run.start_date, run.end_date), attendance_validated=True
    ).values_list("employee_id_id", flat=True)
    ids = set(mapped) | set(attended)
    qs = Employee.objects.filter(pk__in=ids, is_active=True).select_related(
        "employee_work_info", "employee_work_info__department_id"
    )
    if run.department_id:
        qs = qs.filter(employee_work_info__department_id=run.department_id)
    if run.employee_type_id:
        qs = qs.filter(employee_work_info__employee_type_id=run.employee_type_id)
    return list(qs.order_by("badge_id", "employee_first_name"))


def basic_salary_of(emp):
    """The full-month basic salary on the employee record (Work information)."""
    wi = getattr(emp, "employee_work_info", None)
    return D(getattr(wi, "basic_salary", None) or 0)


# ---------------------------------------------------------------- generation
def _editable(run):
    if run.is_final:
        raise PayrollError("This payroll is approved and cannot be recalculated or edited.")


@transaction.atomic
def generate(run):
    """
    Build or rebuild the lines of a draft/reviewed run. Days = validated Horilla attendance (one per day,
    including attendance HR added by hand). Basic salary = the employee record. Fines, loans and salary
    advances = Horilla LoanAccount instalments that are due. HR's per-worker working-days overrides survive a
    rebuild. Nothing is written to the loan ledger here.
    """
    run = PayrollRun.objects.select_for_update().get(pk=run.pk)
    _editable(run)
    weeks = calculators.week_buckets(run.start_date, run.end_date)
    emps = payroll_employees(run)
    ids = [e.pk for e in emps]

    present, waiting = defaultdict(set), defaultdict(int)
    for emp_id, day, validated in Attendance.objects.filter(
        employee_id__in=ids, attendance_date__range=(run.start_date, run.end_date)
    ).values_list("employee_id_id", "attendance_date", "attendance_validated"):
        if validated:
            present[emp_id].add(day)
        else:
            waiting[emp_id] += 1

    collected = defaultdict(set)
    for loan_id, no in LoanDeduction.objects.filter(loan__employee_id__in=ids).values_list("loan_id", "installment_no"):
        collected[loan_id].add(no)
    loans = defaultdict(list)
    for loan in LoanAccount.objects.filter(employee_id__in=ids, settled=False):
        loans[loan.employee_id_id].append(loan)

    existing = {l.employee_id: l for l in run.lines.all()}
    for emp_id in set(existing) - set(ids):
        existing[emp_id].delete()

    total_net = Decimal("0")
    for e in emps:
        days = present.get(e.pk, set())
        week_counts = [sum(1 for d in days if a <= d <= b) for a, b in weeks]
        line = existing.get(e.pk) or PayrollLine(run=run, employee=e)
        working = line.working_days_override if line.working_days_override is not None else run.default_working_days
        basic = basic_salary_of(e)

        fine_items, loan_items = [], []
        fine_total = iou = Decimal("0")
        for loan in loans.get(e.pk, []):
            for k, due, amt in due_items(loan, run.end_date, collected[loan.pk]):
                item = {"loan": loan.pk, "title": loan.title, "type": loan.get_type_display(), "no": k, "of": loan.installments,
                        "due": due.isoformat(), "amount": str(amt)}
                if is_fine(loan):
                    fine_items.append(item)
                    fine_total += amt
                else:
                    loan_items.append(item)
                    iou += amt

        flags = []
        if waiting.get(e.pk):
            flags.append(f"{waiting[e.pk]} attendance day(s) waiting for validation - not counted")
        if not days:
            flags.append("No validated attendance in range")
        if not basic:
            flags.append("No basic salary on the employee record")
        if working and sum(week_counts) > working:
            flags.append("Days attended exceed working days")
        if not working:
            flags.append("Working days not set")
            earned = Decimal("0")
        else:
            earned = amount_earned(sum(week_counts), working, basic, fine_total, iou)
            if earned < 0:
                flags.append("Net pay is negative")

        wi = getattr(e, "employee_work_info", None)
        line.emp_code = e.badge_id or ""
        line.first_name, line.last_name = e.employee_first_name or "", e.employee_last_name or ""
        line.department = str(wi.department_id) if wi and wi.department_id_id else ""
        line.week_days, line.total_days = week_counts, sum(week_counts)
        line.working_days = working
        line.basic_salary, line.fines, line.iou, line.amount_earned = basic, fine_total, iou, earned
        line.flags = flags
        line.breakdown = {
            "formula": f"{sum(week_counts)} / {working} x {basic} - {fine_total} - {iou}",
            "fines": fine_items, "loans": loan_items,
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
        out.append("The run has no workers. Check the dates, department and worker category, and that the workers are mapped or have validated attendance.")
    for l in lines:
        label = f"{l.emp_code} {l.first_name}".strip()
        if not l.working_days:
            out.append(f"{label}: working days not set")
        if not l.basic_salary:
            out.append(f"{label}: no basic salary on the employee record (Employee > Work information)")
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
    Approve: re-checks that every instalment is still unused, writes the ledger and locks the run.
    Anything stale means recalculate.
    """
    run = PayrollRun.objects.select_for_update().get(pk=run.pk)
    if run.status != PayrollRun.REVIEWED:
        raise PayrollError("Only a reviewed payroll can be approved.")
    problems = blockers(run)
    if problems:
        raise PayrollError("; ".join(problems[:5]))

    clash = (
        PayrollLine.objects.filter(
            run__status__in=PayrollRun.FINAL, run__start_date__lte=run.end_date,
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

    taken = []
    try:
        with transaction.atomic():
            for line in run.lines.all():
                for item in line.breakdown.get("loans", []) + line.breakdown.get("fines", []):
                    LoanDeduction.objects.create(
                        loan_id=item["loan"], installment_no=item["no"], due_date=date.fromisoformat(item["due"]),
                        amount=D(item["amount"]), run=run,
                    )
                    taken.append(item["loan"])
    except IntegrityError as exc:
        raise PayrollError(
            "A loan, advance or fine instalment in this run was already taken by another approved run. Recalculate."
        ) from exc

    # A loan whose every instalment has now been taken is marked settled on Horilla's loan too.
    for loan_id in set(taken):
        loan = LoanAccount.objects.get(pk=loan_id)
        if loan.payroll_deductions.count() >= int(loan.installments or 0):
            LoanAccount.objects.filter(pk=loan_id).update(settled=True, settled_date=timezone.now())

    run.status, run.approved_by, run.approved_at = PayrollRun.APPROVED, user, timezone.now()
    run.save()
    return run
