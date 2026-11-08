# weekly_payroll (Phase 4)

Approved daily attendance -> payroll run (any date range) -> reviewed -> approved. Excel sheet downloadable at any time.

## The calculation (confirmed with Demiller)
`AMOUNT EARNED = ROUND(TOTAL DAY / WORKING DAYS x BASIC SALARY, 2) - FINES - IOU`  (nearest kobo, half up, `Decimal` throughout)
- TOTAL DAY = approved *present* days in the range (never raw punches; unapproved days are flagged, not counted).
- WORKING DAYS = entered by HR when creating the run (default for all) and overridable per worker. Never hard-coded.
- BASIC SALARY = the worker's **full-month** amount from an approved pay profile effective on the run's end date.
- Negative net, days > working days, no approved attendance are flagged on the line (not silently clamped).
- WEEK 1..n = one column per 7 days from the run start; the last may be shorter.

## Fines
HR issues a fine (fine types hold default amounts), Management approves/waives. Only approved, unwaived fines are
deducted, **in full by the first run that gets approved after approval**; each fine is applied once. Nothing is created from
attendance, and Horilla's own late/early penalties are untouched.

## Loans (extends Horilla `LoanAccount`)
Create the loan in Payroll > Loans as usual, then add a **repayment agreement** (weekly or monthly instalment, first due date);
Management approves. Instalment k falls due on first-due + (k-1) weeks/months. A run collects every uncollected instalment due on
or before its end date, capped at the outstanding balance. Weekly runs therefore never take a monthly instalment early.
Ledger rows (`LoanRepayment`) are written **only when a run is approved**; draft calculations and recalculation never consume an
instalment; the database refuses to collect the same instalment twice. Early settlement and authorised adjustments need a note;
a cleared loan is marked settled on the Horilla loan too.

## Lifecycle and locking
Draft -> Reviewed (HR Officer or Approver) -> Approved (Approver only). Editing working days sends a reviewed run back to draft.
Approved runs can't be recalculated or edited (service and model both refuse). Approval re-checks every instalment and fine and
refuses with "recalculate" if another run used them first, and refuses overlapping approved periods for the same worker.
Exported/Paid states arrive with the bank export (Phase 5).

## Roles
| Group | Payroll powers |
|---|---|
| HR Officer | create/recalculate runs, set working days, mark reviewed, propose pay profiles/fines/loan agreements |
| HR/Payroll Approver | everything on runs + **approve payroll** |
| Management | approve pay profiles, fines (and waive), loan agreements; view payroll |
| Accountant | view and download **approved** runs only |

## Not reusing Horilla's PayrollBatch/Payslip (deliberate)
Its engine builds float payslips from contracts and salary structures; it cannot express this sheet. Employees, departments,
bank details and `LoanAccount` are still Horilla's. A payslip link can be added later if wanted.
