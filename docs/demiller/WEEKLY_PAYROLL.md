# Weekly payroll (weekly_payroll)

Everything the payroll needs is read from Horilla's own records. A payroll run is for **any date range**; the Excel sheet can be downloaded at any status.

## Where each number comes from
| Sheet column | Source |
|---|---|
| WEEK 1..n, TOTAL DAY | **validated** Horilla attendance (Attendance > Attendances), one per worker per day, including attendance HR added by hand. One column per 7 days from the run start. |
| WORKING DAYS | entered by HR when creating the run (default for all), overridable per worker. |
| BASIC SALARY | the worker's **employee record**: Employee > Work information > Basic Salary (full month). Change it there and Recalculate. |
| FINES | Horilla **fines**: Payroll > Loans > Fine (a loan-type record of type "Penalty / Fine"). |
| IOU | Horilla **loans and salary advances** (Payroll > Loans > Loan / Salary Advance). |
| AMOUNT EARNED | `ROUND(TOTAL DAY / WORKING DAYS x BASIC SALARY, 2) - FINES - IOU`, to the kobo, `Decimal` throughout. |

Who is in a run: active workers who are mapped to a biometric ID or have validated attendance in the range; optionally limited to a department
and/or worker category. A worker with no validated days shows 0 days (so HR can see who is missing).

## Fines, loans and salary advances (Horilla's Payroll > Loans)
Create them on Horilla's Loans page as usual (loan amount, number of instalments, instalment start date). Horilla schedules them as equal **monthly**
instalments from the start date (instalment k is due k-1 months after the start date; the last instalment absorbs any kobo left over).
A payroll run takes every instalment that is **due on or before the run's end date and not yet taken**. A fine with one instalment is therefore taken
**in full by the first run that ends on or after its date**.
* A monthly instalment is **not** taken by earlier weekly runs.
* Instalments are written to a ledger only when a run is **approved** (drafts and recalculation never use one up); the database refuses to take the same instalment twice,
  and approval re-checks and refuses ("Recalculate") if another run took it first.
* When every instalment has been taken, the Horilla loan is marked **Settled**. A loan you mark Settled in Horilla is skipped.
* Early settlement: mark the loan Settled in Horilla. (Weekly instalments are not supported by Horilla's loans, so they are not supported here.)
* There is no approval step for loans and fines: Horilla's own permissions control who can create them (HR Officer has them).

## Lifecycle and locking
Draft > Reviewed (HR Officer or Approver) > Approved (Approver only) > Exported > Paid (bank export). Changing working days sends a reviewed run back to draft.
Approved runs cannot be recalculated or edited; approval also refuses overlapping approved periods for the same worker. Blockers for review: working days missing,
**no basic salary on the employee record**, or no workers.

## Roles
HR Officer: create/recalculate runs, working days, mark reviewed, loans/fines in Horilla. HR/Payroll Approver: approve payroll. Management: view. Accountant: approved runs only.

## What changed from the first version
The separate pay-profile (with approval), fine and loan-agreement screens were removed. Instalments that approved runs had already collected were carried across
to the new ledger so they cannot be taken twice. Not reused: Horilla's PayrollBatch/Payslip (its float-based, contract-driven engine cannot express this sheet).
