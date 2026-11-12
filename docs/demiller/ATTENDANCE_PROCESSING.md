# Biometric attendance (attendance_processing)

Attendance now lives on **Horilla's own Attendance > Attendances page**. This app only turns biometric punches into
ordinary Horilla attendance records.

```
device .dat file -> Biometric Import (upload, map IDs) -> Biometric Attendance > "Add punches to Attendances"
   -> Attendance > Attendances (Validate tab: validate in bulk, edit, add by hand) -> Payroll counts validated days
```

## What "Add punches to Attendances" does
For the chosen date range (any dates, or whole months; max 93 days; optionally one department / worker category) it creates
**one ordinary Horilla attendance record per worker per day** for every valid punch that has no record yet:
check-in = first punch of the day; check-out only when a later punch is at least an hour after the first.
It never touches an existing record (validated, edited or added by hand), so you can run it again after every upload.

* The day is the **Africa/Lagos calendar date** of the punch. Any number of punches in a day is one day.
* Days before the worker's joining date or after the contract end date are ignored; future days are never added.
* Only workers with a verified biometric mapping are processed.

## Attendance rules (windows)
A rule lists the time-of-day **windows** in which a punch counts, e.g. night shift `00:00-09:00` and afternoon shift `14:00-20:00`
(Attendance > Biometric Attendance > Attendance rules). The most specific rule wins: worker, worker category, department, shift,
everyone. With no rule any punch time counts. Punches outside every window, or punches in two different windows on one day, are **not added**;
they show as red dots ("needs attention"). Open the day to see the punches and **Add to Attendances anyway** if it is genuine.

## Validating (Horilla's own page)
Attendance > Attendances has the **Validate Attendances** tab (waiting), **Validated Attendances**, and **OT Attendances**.
Select records and validate them in bulk. **Payroll counts validated days only**; waiting days are flagged on the payroll line.
You can also create attendance there by hand (Create); once validated it counts like any other day.

**OT Attendances** is Horilla's overtime tab: validated records whose worked hours exceed the shift minimum. Casual workers who punch once a day
have no worked hours, so it stays empty. The payroll sheet does not use overtime.

## Overview grid (Biometric Attendance)
One row per worker, one column per day plus a column per 7 days (Wk1..): green = validated, amber = waiting for validation,
red = needs attention. Totals and filters (dates or months, department, worker category, "only workers needing attention").

## Roles
* **HR Officer / HR-Payroll Approver**: process punches, validate in Horilla (they carry Horilla's `view/add/change attendance`), rules (Approver edits).
* Approver edits rules; Accountant and Management can only view attendance.
* Assign roles per company (Horilla applies a group's permissions only in companies where the user holds that group).

## What changed from the first version
The separate daily-attendance table, weekly approval/lock, reopen and correction audit were removed in favour of Horilla's own validation.
The raw punches are untouched. If you had already approved attendance in the old screens, run **Add punches to Attendances** again for that range
and validate in Horilla.
