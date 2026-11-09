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

## C. Attendance (HR Officer, then Approver)
| # | Check | Expected | Pass |
|---|---|---|---|
| C1 | Process a range (days to days, and month to month) | one status per worker per day | |
| C2 | Worker with 2 punches in one day | counts as 1 present day | |
| C3 | Day with no punch | Missing (needs review); future days untouched | |
| C4 | Approved leave / public holiday / rest day | not Missing | |
| C5 | Punch outside the attendance window | Review, not discarded | |
| C6 | Correct a day without a reason | refused; with a reason: saved and shown in history | |
| C7 | HR Officer tries to approve | not allowed | |
| C8 | Approver approves while Missing/Review remain | blocked until resolved | |
| C9 | Approved day: try to correct / re-process | locked | |
| C10 | Approver reopens with a reason | days editable again; reopen recorded | |

## D. Payroll (HR Officer, Approver, Management)
| # | Check | Expected | Pass |
|---|---|---|---|
| D1 | Pay profile proposed by HR, approved by Management | only approved profile is used | |
| D2 | Create a run for any date range with working days | each worker's amount = days / working days x basic - fines - IOU, to the kobo | |
| D3 | Compare 3 workers with the manual sheet | identical | |
| D4 | Present-but-unapproved attendance | flagged, not counted | |
| D5 | Change one worker's working days | amount updates; reviewed run returns to draft | |
| D6 | Fine: HR issues, Management approves | deducted in full in the first approved run only; unapproved fines never | |
| D7 | Monthly loan (e.g. 20,000) with weekly runs | not deducted in weekly runs; deducted once in the run covering the due date | |
| D8 | Loan balance never goes below 0; early settlement needs a note | as stated | |
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
