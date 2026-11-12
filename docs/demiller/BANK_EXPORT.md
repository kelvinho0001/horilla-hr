# bank_export (Phase 5)

Approved payroll -> validated payment workbook (Excel) -> history -> Paid.

## Setup (once)
1. Load the bank list: **Payroll > Bank Export > Bank list > Upload** (CSV/XLSX with `CODE,NAME`), or
   `python manage.py load_banks docs/demiller/bank_codes.csv` (the 87 banks from Demiller's template).
   Codes are text (`044`, `035A`); in Excel format the CODE column as Text before saving a CSV.
2. **Worker bank accounts** lists **every active employee** with what the employee record says (Employee > Bank details, as typed), the bank it matched in the list, a masked account and a status; filter by name/ID, department or "needing attention". Fix a worker with **Edit** (bank drop-down + 10-digit account number).
   This writes Horilla's own `EmployeeBankDetails` (account number, bank name, bank code) and records which listed bank
   it is, so the code always comes from the list. Existing Horilla bank records resolve automatically by bank code, then
   by bank name ignoring case, spacing and words like "Bank"/"Plc" ("ACCESS BANK PLC" = Access Bank); anything else is shown with what was typed and "Bank not selected from the bank list". Lists show only the last 4 digits.

## Exporting
- Only an **approved** payroll can be exported. The page shows employee count, total payment, narration, and every worker
  with a missing/invalid bank or account. Workers with zero or negative net are skipped, not exported.
- Problems block the export. The accountant may tick "export valid workers only"; the left-out workers are recorded on the file.
- The workbook has **PAYROLL** (the Demiller sheet, Table1, live formulas), **PAYMENT SCHEDULE** (Table3: EMP ID, FIRST NAME,
  LAST NAME, DEPARTMENT, ACCOUNT NUMBER, BENEFICIARY NAME, AMOUNT, BANK CODE, NARRATION), **BANK CODES** and **RUN INFO**.
  PAYMENT SCHEDULE holds the final figures as values (not formulas) so excluded workers cannot shift rows; account numbers and bank
  codes are stored as text. Columns/order/headers come from the default `PaymentTemplate` (editable in admin) - the sample sheet is
  the default for every bank.
- Beneficiary name = FIRST LAST in capitals. Narration = the run's narration, else "<MONTH> SALARY - DEMILLER FARMS"
  (`PAYROLL_COMPANY_NAME` setting).
- Reference `DFP-<run id>-<n>`; the file is kept in the database (not /media) and downloaded through a permission-checked view.

## Duplicate protection and status
One live payment file per payroll - enforced by a database constraint. Payroll status: Approved -> **Exported** -> **Paid**.
A file can only be **superseded** (reason required, Approver) while unpaid, which returns the payroll to Approved so a corrected
file can be generated; the old file stays in history. **Paid** is final. Exported/Paid payroll is as locked as Approved.

## Roles
Accountant: bank list, generate, download, mark paid, view masked accounts. HR Officer: edit worker bank accounts.
HR/Payroll Approver: supersede. Management: view exports. Only the app creates files; nothing is sent to a bank.
