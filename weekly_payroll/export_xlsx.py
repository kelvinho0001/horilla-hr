"""
The Demiller payroll sheet as Excel: same layout as PAYROLL_TEMPLATE_1.xlsx
(table "Table1", live formulas) pre-filled from the run. One WEEK column per
7 days of the run. AMOUNT EARNED is ROUND(...,2) so Excel agrees with the
kobo rounding stored in Horilla. Downloadable at any status; a RUN INFO sheet
says whether it is still a draft.
"""

from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo

R = "Table1[[#This Row],[{}]]"


def build_workbook(run):
    wb = populate_workbook(Workbook(), run)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def populate_workbook(wb, run):
    """Fill ``wb`` (a fresh Workbook) with PAYROLL and RUN INFO; bank_export adds the bank sheets."""
    lines = list(run.lines.all())
    n_weeks = max((len(l.week_days) for l in lines), default=1)
    week_cols = [f"WEEK {i}" for i in range(1, n_weeks + 1)]
    headers = (
        ["EMP ID", "FIRST NAME", "LAST NAME", "DEPARTMENT"] + week_cols
        + ["TOTAL DAY", "WORKING DAYS", "BASIC SALARY", "FINES", "IOU", "AMOUNT EARNED"]
    )
    ws = wb.active
    ws.title = "PAYROLL"
    ws.append(headers)
    total_formula = "=" + "+".join(R.format(w) for w in week_cols)
    earned_formula = "=ROUND({t}/{w}*{b},2)-{f}-{i}".format(
        t=R.format("TOTAL DAY"), w=R.format("WORKING DAYS"), b=R.format("BASIC SALARY"),
        f=R.format("FINES"), i=R.format("IOU"),
    )
    for l in lines:
        days = list(l.week_days) + [0] * (n_weeks - len(l.week_days))
        ws.append(
            [l.emp_code, l.first_name, l.last_name, l.department] + days
            + [total_formula, l.working_days, float(l.basic_salary), float(l.fines), float(l.iou), earned_formula]
        )
    last_col, last_row = get_column_letter(len(headers)), max(len(lines), 1) + 1
    table = Table(displayName="Table1", ref=f"A1:{last_col}{last_row}")
    table.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showRowStripes=True)
    ws.add_table(table)
    money_start = len(headers) - 3  # BASIC SALARY..AMOUNT EARNED
    for row in ws.iter_rows(min_row=2, min_col=money_start - 0, max_col=len(headers)):
        for c in row:
            c.number_format = "#,##0.00"
    for i, h in enumerate(headers, 1):
        ws.column_dimensions[get_column_letter(i)].width = max(12, len(h) + 2)

    info = wb.create_sheet("RUN INFO")
    rows = [
        ("Run", run.name), ("Period", f"{run.start_date} to {run.end_date}"),
        ("Status", run.get_status_display().upper() + ("" if run.is_final else " - NOT APPROVED, figures may change")),
        ("Default working days", run.default_working_days), ("Narration", run.narration),
        ("Workers", run.employee_count), ("Total net payable", float(run.total_net)),
        ("Rounding", "Nearest kobo"),
    ]
    for r in rows:
        info.append(r)
    info["A1"].font = Font(bold=True)
    info.column_dimensions["A"].width = 24
    info.column_dimensions["B"].width = 50
    return wb
