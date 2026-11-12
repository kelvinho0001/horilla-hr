# User acceptance test (UAT)

Use a **staging copy** or a test company with a few dummy workers. Tester ticks Pass/Fail and signs.
Roles: log in as a different user for each role.

## A. Setup
| # | Check | Expected | Pass |
|---|---|---|---|
| A1 | `python manage.py demiller_check` | 0 failures | |
| A2 | Open the site over HTTPS; http redirects to https | padlock, no warnings | |
| A3 | Bank list loaded | ~87 banks, `044` shown with its zero | |
| A4 | Roles: each user only sees the menus for their role | IT sees Biometric Import only; Accountant sees Bank Export | |

## B. Biometric import (IT Administrator / HR Officer)
| # | Check | Expected | Pass |
|---|---|---|---|
| B1 | Upload a real weekly report from Time.Net | rows imported, errors listed with row numbers | |
| B2 | Upload the same file again | 0 imported, all duplicates, no new records | |
| B3 | Unknown biometric ID | appears under Unmapped IDs; map it; its earlier punches attach | |
| B4 | Same ID mapped to two employees | refused | |
| B5 | Report with a bad date / future date | reported, other rows still import | |

## C. Attendance (HR Officer, Approver)
| # | Check | Expected | Pass |
|---|---|---|---|
| C1 | Biometric Attendance > Add punches to Attendances (days to days, and month to month) | one Horilla attendance record per worker per day, waiting for validation | |
| C2 | Worker with 2 punches in one day | one record | |
| C3 | Same range processed again | nothing duplicated; validated records untouched | |
| C4 | Punch outside the attendance windows / punches in two shifts | not added; red dot; "Add to Attendances anyway" works | |
| C5 | Attendance > Attendances > Attendance To Validate | the records are there; bulk validate works for an HR Officer | |
| C6 | Create an attendance by hand in Attendances and validate it | it appears green on Biometric Attendance and counts in payroll | |
| C7 | Future days, days before joining, workers with no mapping | nothing added | |

## D. Payroll (HR Officer, Approver)
| # | Check | Expected | Pass |
|---|---|---|---|
| D1 | Basic salary set on Employee > Work information | the run uses it; changing it and pressing Recalculate changes the amount | |
| D2 | Create a run for any date range with working days | each worker's amount = days / working days x basic - fines - IOU, to the kobo | |
| D3 | Compare 3 workers with the manual sheet | identical | |
| D4 | Attendance still waiting for validation | flagged on the line, not counted | |
| D5 | Change one worker's working days | amount updates; a reviewed run goes back to draft | |
| D6 | Fine created in Payroll > Loans (Fine tab, 1 instalment) | in the FINES column in full, once; the loan shows Settled after the run is approved | |
| D7 | Loan / salary advance (monthly instalments) | only instalments due on or before the run's end are taken; never twice; never by an overlapping run | |
| D8 | Worker with no basic salary | flagged; Mark reviewed refused | |
| D9 | HR Officer tries to approve payroll | not allowed; Approver can | |
| D10 | Approved run: recalculate / edit | refused | |
| D11 | Download Excel at draft and at approved | draft marked DRAFT; layout matches the template; formulas recalculate in Excel | |

## E. Bank export (Accountant, Approver)
| # | Check | Expected | Pass |
|---|---|---|---|
| E1 | Worker with no bank / bad account number | listed; export blocked (or excluded if ticked) | |
| E2 | Generate file | count and total equal the payroll; account numbers and bank codes keep leading zeros | |
| E3 | Open `PAYMENT SCHEDULE` and compare with the sample the bank accepts | accepted by the bank's upload (the accountant confirms) | |
| E4 | Generate a second file for the same payroll | refused | |
| E5 | Supersede with a reason, regenerate | new reference; old one kept in history | |
| E6 | Mark paid; try to supersede | blocked; payroll shows Paid | |
| E7 | HR Officer tries to generate the file | not allowed | |

## F. Production readiness
| # | Check | Expected | Pass |
|---|---|---|---|
| F1 | Backup ran last night and a copy is off-server | files present | |
| F2 | Restore tested on a scratch database | app opens, data present | |
| F3 | Uptime monitor on `/health/` | alerts on stop | |
| F4 | Admin password changed; leaver accounts removed | done | |

Tester: ______  Date: ______  Result: ______   Approved for go-live by: ______
