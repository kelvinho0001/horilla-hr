# attendance_processing (Phase 3)

Raw punches (`biometric_integration.RawTransaction`) -> `DailyAttendance` (one row per employee per day) -> HR review ->
`AttendanceApproval` (locks days). Payroll (Phase 4) reads **approved** days only.

## Rules of the day
- Several punches on a day = one day. All punches stay in `RawTransaction`.
- The day is the **Africa/Lagos** calendar day (`ATTENDANCE_TIMEZONE` setting), not UTC and not Horilla's `TIME_ZONE`.
- Status: Present, Missing (no punch on a working day), Review (punches, none inside the window), Absent, Leave, Holiday, Rest.
  Missing/Review block approval until HR resolves them (correction, or "mark all missing as absent" with a reason).
- Future dates are never marked. Days before joining / after contract end are skipped.
- Leave = approved Horilla leave requests. Holidays = Horilla `Holidays` (incl. department/position/employee-specific).
- `AttendanceRule`: window and working weekdays per employee > worker category > department > shift > everyone.
  Nothing is hard-coded; with no rule any punch counts and Mon-Sat are working days (assumption, change via a global rule).
- Only employees with an active, verified biometric mapping are processed.

## Ranges
Review any range: exact dates, or whole months (from month / to month). Max 93 days. One column per 7 days (Wk1..n) is shown
beside the daily grid; the same `week_buckets()` will feed the payroll sheet.

## Approval
Approve a range (optionally one department): locked days cannot be corrected or re-processed. Reopen needs a reason and is
recorded (who/when/why); days then become editable again. Corrections need a reason and are kept in `AttendanceCorrection`.

## Roles and permissions (how Horilla does it)
Horilla uses Django **Groups + permissions**, checked with `permission_required(...)` and sidebar `accessibility`.
Because `COMPANY_SCOPED_PERMISSIONS` is on by default, a group only applies in a company where the user has a
`CompanyGroupAssignment` (set on the user's role screen in Horilla). Groups are seeded after migrate (additive, never strips
customisations) by `attendance_processing/permissions.py`:

| Group | Can |
|---|---|
| HR Officer | view, process, correct attendance; upload reports; manage mappings. **Cannot approve or reopen.** |
| HR/Payroll Approver | everything above + **approve / reopen attendance** + manage rules (payroll approval is added in Phase 4) |
| Accountant | view attendance/approvals (bank export permissions in Phase 5) |
| IT Administrator | biometric import, providers, mappings |

Custom permissions: `process_attendance`, `correct_attendance`, `approve_attendance`, `reopen_attendance`.
Assign users to groups; do not give everyone the approver group.
