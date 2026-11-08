"""Bank directory import, bank resolution, validation and payment-file export."""

import csv
import hashlib
import io
import re
from decimal import Decimal

import openpyxl
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from employee.models import EmployeeBankDetails
from weekly_payroll.models import PayrollRun

from .models import DEFAULT_COLUMNS, Bank, EmployeeBankLink, ExportBatch, PaymentTemplate


class ExportError(Exception):
    def __init__(self, message, problems=None):
        super().__init__(message)
        self.problems = problems or []


def account_length():
    return getattr(settings, "BANK_ACCOUNT_LENGTH", 10)


# ------------------------------------------------------------ bank directory
def load_bank_rows(file_obj, filename):
    """Read (code, name) pairs from CSV/XLSX with CODE and NAME headers. Returns (rows, errors)."""
    name = (filename or "").lower()
    if name.endswith((".xlsx", ".xlsm")):
        ws = openpyxl.load_workbook(file_obj, read_only=True, data_only=True).worksheets[0]
        raw = [list(r) for r in ws.iter_rows(values_only=True)]
    else:
        data = file_obj.read()
        text = data.decode("utf-8-sig") if isinstance(data, bytes) else data
        raw = list(csv.reader(io.StringIO(text)))
    raw = [r for r in raw if any(str(c).strip() for c in r if c is not None)]
    if not raw:
        raise ExportError("The file is empty.")
    header = [str(h or "").strip().lower() for h in raw[0]]
    if "code" not in header or "name" not in header:
        raise ExportError("The file needs CODE and NAME columns.")
    ci, ni = header.index("code"), header.index("name")
    rows, errors = [], []
    for n, r in enumerate(raw[1:], start=2):
        code = str(r[ci]).strip() if ci < len(r) and r[ci] is not None else ""
        nm = str(r[ni]).strip() if ni < len(r) and r[ni] is not None else ""
        if not code or not nm:
            errors.append((n, "CODE and NAME are both required"))
        elif not re.fullmatch(r"[A-Za-z0-9]{2,20}", code):
            errors.append((n, f"Invalid code '{code}'"))
        else:
            rows.append((code, nm))
    return rows, errors


@transaction.atomic
def import_banks(rows):
    created = updated = 0
    seen = {}
    for code, name in rows:
        seen[code] = name  # a repeated code: the last row wins
    for code, name in seen.items():
        _b, was_created = Bank.objects.update_or_create(code=code, defaults={"name": name, "is_active": True})
        created += was_created
        updated += not was_created
    return {"created": created, "updated": updated, "duplicates_in_file": len(rows) - len(seen)}


# ------------------------------------------------------------ worker accounts
def resolve_bank(employee):
    """(bank, account_number, issues) for an employee, from the link, else by code or name."""
    details = EmployeeBankDetails.objects.filter(employee_id=employee).select_related("bank_link__bank").first()
    if details is None:
        return None, "", ["No bank details"]
    issues, bank = [], None
    link = getattr(details, "bank_link", None)
    if link:
        bank = link.bank
    else:
        if details.any_other_code1:
            bank = Bank.objects.filter(code=details.any_other_code1.strip()).first()
        if bank is None and details.bank_name:
            bank = Bank.objects.filter(name__iexact=details.bank_name.strip()).first()
    if bank is None:
        issues.append("Bank not selected from the bank list")
    elif not bank.is_active:
        issues.append("Bank is inactive")
    acct = (details.account_number or "").strip()
    if not acct:
        issues.append("Missing account number")
    elif not acct.isdigit() or len(acct) != account_length():
        issues.append(f"Account number must be {account_length()} digits")
    return bank, acct, issues


@transaction.atomic
def save_worker_bank(employee, bank, account_number):
    acct = (account_number or "").strip()
    if not acct.isdigit() or len(acct) != account_length():
        raise ExportError(f"Account number must be {account_length()} digits.")
    others = EmployeeBankDetails.objects.exclude(employee_id=employee).filter(account_number=acct)
    if others.exists():
        raise ExportError("Another worker already has this account number.")
    details = EmployeeBankDetails.objects.filter(employee_id=employee).first()
    if details is None:
        details = EmployeeBankDetails(employee_id=employee)
    details.account_number, details.bank_name, details.any_other_code1 = acct, bank.name, bank.code
    details.save()
    EmployeeBankLink.objects.update_or_create(details=details, defaults={"bank": bank})
    return details


def mask(acct):
    return ("•" * max(len(acct) - 4, 0) + acct[-4:]) if acct else ""


# ------------------------------------------------------------ preflight + export
def default_template():
    t = PaymentTemplate.objects.filter(is_default=True).first()
    return t.name if t else "Built-in default", (t.columns if t and t.columns else DEFAULT_COLUMNS)


def default_narration(run):
    if run.narration:
        return run.narration
    company = getattr(settings, "PAYROLL_COMPANY_NAME", "DEMILLER FARMS")
    return f"{run.end_date:%B} SALARY - {company}".upper()


def preflight(run):
    """Rows ready to pay and problems, without writing anything."""
    ok, problems = [], []
    for line in run.lines.select_related("employee"):
        label = f"{line.emp_code} {line.first_name} {line.last_name}".strip()
        if line.amount_earned <= 0:
            problems.append({"line": line, "label": label, "issues": ["Nothing payable (net is zero or negative)"], "blocking": False})
            continue
        bank, acct, issues = resolve_bank(line.employee)
        if issues:
            problems.append({"line": line, "label": label, "issues": issues, "blocking": True})
        else:
            ok.append({"line": line, "bank": bank, "account": acct})
    return {
        "ok": ok, "problems": problems,
        "blocking": [p for p in problems if p["blocking"]],
        "count": len(ok), "total": sum((r["line"].amount_earned for r in ok), Decimal("0")),
    }


def _value(field, item, run, narration, reference):
    line = item["line"]
    return {
        "emp_id": line.emp_code, "first_name": line.first_name, "last_name": line.last_name,
        "department": line.department, "account_number": item["account"],
        "beneficiary_name": f"{line.first_name} {line.last_name}".strip().upper(),
        "amount": line.amount_earned, "bank_code": item["bank"].code, "bank_name": item["bank"].name,
        "narration": narration, "reference": reference,
    }[field]


def build_workbook(run, items, narration, reference, columns):
    from openpyxl import Workbook
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.table import Table, TableStyleInfo
    from weekly_payroll.export_xlsx import populate_workbook

    wb = populate_workbook(Workbook(), run)

    ws = wb.create_sheet("PAYMENT SCHEDULE", 1)
    ws.append([c["header"] for c in columns])
    text_fields = {"account_number", "bank_code", "emp_id", "reference"}
    for item in items:
        ws.append([_value(c["field"], item, run, narration, reference) for c in columns])
    for ci, c in enumerate(columns, start=1):
        for r in range(2, len(items) + 2):
            cell = ws.cell(row=r, column=ci)
            if c["field"] in text_fields:
                cell.value = str(cell.value)
                cell.number_format = "@"          # keeps leading zeros
                cell.data_type = "s"
            elif c["field"] == "amount":
                cell.value = float(cell.value)
                cell.number_format = '_-[$₦-46A]* #,##0.00_-;\\-[$₦-46A]* #,##0.00_-;_-[$₦-46A]* "-"??_-;_-@_-'
        ws.column_dimensions[get_column_letter(ci)].width = max(14, len(c["header"]) + 2)
    tbl = Table(displayName="Table3", ref=f"A1:{get_column_letter(len(columns))}{max(len(items), 1) + 1}")
    tbl.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showRowStripes=True)
    ws.add_table(tbl)

    bk = wb.create_sheet("BANK CODES")
    bk.append(["CODE", "NAME"])
    for b in Bank.objects.filter(is_active=True).order_by("name"):
        bk.append([b.code, b.name])
    for row in bk.iter_rows(min_row=2, max_col=1):
        row[0].number_format = "@"
        row[0].data_type = "s"
    bk.column_dimensions["A"].width = 12
    bk.column_dimensions["B"].width = 40

    info = wb["RUN INFO"]
    info.append(("Export reference", reference))
    info.append(("Payments", len(items)))
    info.append(("Total payment", float(sum(i["line"].amount_earned for i in items))))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@transaction.atomic
def export_run(run, user, allow_partial=False):
    run = PayrollRun.objects.select_for_update().get(pk=run.pk)
    if run.status in (PayrollRun.EXPORTED, PayrollRun.PAID):
        raise ExportError("A payment file already exists for this payroll. Supersede it first if it must be regenerated.")
    if run.status != PayrollRun.APPROVED:
        raise ExportError("Only an approved payroll can be exported.")
    pf = preflight(run)
    if pf["blocking"] and not allow_partial:
        raise ExportError(
            f"{len(pf['blocking'])} worker(s) have missing or invalid bank details.", pf["blocking"]
        )
    if not pf["ok"]:
        raise ExportError("There is nothing payable to export.", pf["problems"])

    seq = run.exports.count() + 1
    reference = f"DFP-{run.pk:05d}-{seq:02d}"
    narration = default_narration(run)
    tname, columns = default_template()
    content = build_workbook(run, pf["ok"], narration, reference, columns)
    try:
        batch = ExportBatch.objects.create(
            run=run, reference=reference, narration=narration, employee_count=pf["count"],
            total_amount=pf["total"], content=content, sha256=hashlib.sha256(content).hexdigest(),
            template_name=tname, created_by=user if getattr(user, "pk", None) else None,
            excluded=[{"emp": p["label"], "issues": p["issues"]} for p in pf["problems"]],
        )
    except IntegrityError as exc:
        raise ExportError("A payment file already exists for this payroll.") from exc
    run.status = PayrollRun.EXPORTED
    run.save()
    return batch


@transaction.atomic
def supersede(batch, user, reason):
    batch = ExportBatch.objects.select_for_update().get(pk=batch.pk)
    if not (reason or "").strip():
        raise ExportError("A reason is required to supersede a payment file.")
    if batch.status != ExportBatch.GENERATED:
        raise ExportError("Only an unpaid payment file can be superseded.")
    batch.status, batch.superseded_by, batch.superseded_at, batch.supersede_reason = (
        ExportBatch.SUPERSEDED, user, timezone.now(), reason.strip(),
    )
    batch.save()
    run = PayrollRun.objects.select_for_update().get(pk=batch.run_id)
    run.status = PayrollRun.APPROVED
    run.save()
    return batch


@transaction.atomic
def mark_paid(batch, user):
    batch = ExportBatch.objects.select_for_update().get(pk=batch.pk)
    if batch.status != ExportBatch.GENERATED:
        raise ExportError("Only an exported, unpaid file can be marked paid.")
    batch.status, batch.paid_by, batch.paid_at = ExportBatch.PAID, user, timezone.now()
    batch.save()
    run = PayrollRun.objects.select_for_update().get(pk=batch.run_id)
    run.status = PayrollRun.PAID
    run.save()
    return batch
