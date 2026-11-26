"""
Employee import template with a Bank Name dropdown.

The normal import template (Employees > Import > Download template) is unchanged except, when a bank list has been
loaded (Bank Payments > Bank list):
  * the Bank Name column becomes a dropdown of those banks (Excel 365 / 2021+ also suggests as you type the first
    letters; older Excel shows the arrow; Google Sheets and LibreOffice show the dropdown),
  * Bank Code 1 fills itself from the chosen bank (a lookup formula; nobody types a code),
  * Account Number is formatted as text so a leading 0 is kept,
  * the bank list itself rides along on a hidden second sheet. The import reads the first sheet only.
A bank name that is pasted (Excel does not check pasted cells) or typed differently is still matched when the file is
imported, and a name that cannot be matched is reported on its own row.
"""

from .models import Bank

LAST_ROW = 3001  # room for 3,000 employees
NAME, CODE, ACCOUNT = "Bank Name", "Bank Code 1", "Account Number"


def decorate(book, sheet, columns):
    banks = list(Bank.objects.filter(is_active=True).order_by("name").values_list("name", "code"))
    if not banks or NAME not in columns:
        return
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation

    letter = {c: get_column_letter(i) for i, c in enumerate(columns, start=1)}
    ref = book.create_sheet("Bank list")
    ref.append(["Bank Name", "Bank Code"])
    for name, code in banks:
        ref.append([name, str(code)])
    for r in range(2, len(banks) + 2):
        ref.cell(r, 2).number_format = "@"
    ref.sheet_state = "hidden"
    last = len(banks) + 1
    name_col = letter[NAME]

    dv = DataValidation(
        type="list", formula1=f"='Bank list'!$A$2:$A${last}", allow_blank=True, showDropDown=False,
        errorStyle="warning", showErrorMessage=True, errorTitle="Bank not in the list",
        error="This name is not in the bank list. Pick one from the dropdown, or keep it and the import will try to match it.",
        showInputMessage=True, promptTitle="Bank", prompt="Pick the bank. Its code fills in by itself.",
    )
    sheet.add_data_validation(dv)
    dv.add(f"{name_col}2:{name_col}{LAST_ROW}")

    if CODE in letter:
        code_col = letter[CODE]
        for r in range(2, LAST_ROW + 1):
            sheet[f"{code_col}{r}"] = (
                f'=IF({name_col}{r}="","",IFERROR(VLOOKUP({name_col}{r},\'Bank list\'!$A$2:$B${last},2,FALSE),""))'
            )
        sheet[f"{code_col}1"].comment = None
    if ACCOUNT in letter:
        for r in range(2, LAST_ROW + 1):
            sheet[f"{letter[ACCOUNT]}{r}"].number_format = "@"
    # the sample row shows a real bank from the list
    sheet[f"{name_col}2"] = banks[0][0]
