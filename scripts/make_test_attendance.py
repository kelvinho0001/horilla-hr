#!/usr/bin/env python3
"""
Make TEST data for a payroll rehearsal: four test workers (TST001-TST004) that copy the four rows of Demiller's
payroll template, an attendance report for them, and an employee-import file for Horilla.

    python scripts/make_test_attendance.py                       # 28 days ending last Sunday
    python scripts/make_test_attendance.py --start 2026-10-05    # must be a Monday
    python scripts/make_test_attendance.py --company "Demiller Farms"   # exact company name in Horilla

Writes test_attendance.csv and test_employees.csv.
Upload the CSV in Biometric Import. It also contains a repeated punch, an unknown ID and a bad row on purpose,
so you can see duplicates, unmapped IDs and error reporting. Expected payroll results are in
docs/demiller/SETUP_AND_TESTING_GUIDE.md (section 5).
"""

import argparse
import csv
from datetime import date, datetime, timedelta

WEEKS = {  # present days per 7-day block (Mon first)
    "TST001": [3, 5, 6, 1],
    "TST002": [4, 5, 4, 6],
    "TST003": [6, 2, 5, 6],
    "TST004": [6, 6, 6, 6],
}
NAMES = {"TST001": "TEST ONE", "TST002": "TEST TWO", "TST003": "TEST THREE", "TST004": "TEST FOUR"}
BASIC = {"TST001": 70000, "TST002": 55000, "TST003": 70000, "TST004": 70000}
DEPTS = {"TST001": "Selecting", "TST002": "Production", "TST003": "Packaging", "TST004": "Logistics"}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start", help="first day, YYYY-MM-DD, must be a Monday")
    p.add_argument("--out", default="test_attendance.csv")
    p.add_argument("--employees-out", default="test_employees.csv")
    p.add_argument("--company", default="Demiller Farms", help="exact name of the company in Horilla")
    args = p.parse_args()
    if args.start:
        start = date.fromisoformat(args.start)
    else:
        today = date.today()
        last_sunday = today - timedelta(days=today.weekday() + 1)
        start = last_sunday - timedelta(days=27)
    if start.weekday() != 0:
        raise SystemExit("--start must be a Monday")
    if start + timedelta(27) >= date.today():
        raise SystemExit("The 28 days must be in the past (attendance in the future is never marked).")

    rows = []
    for badge, weeks in WEEKS.items():
        for w, n in enumerate(weeks):
            for i in range(n):
                day = start + timedelta(7 * w + i)
                rows.append((badge, NAMES[badge], day.strftime("%d/%m/%Y"), "16:0%d:00" % (i % 6)))
    rows.append(("TST001", NAMES["TST001"], start.strftime("%d/%m/%Y"), "16:09:30"))   # 2nd punch same day: still 1 day
    rows.append(("TST999", "NOBODY KNOWN", start.strftime("%d/%m/%Y"), "16:00:00"))   # unknown ID -> Unmapped IDs
    rows.append(("TST002", NAMES["TST002"], "not-a-date", "16:00:00"))                 # bad row -> reported, rest import
    with open(args.out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Emp ID", "Name", "Date", "Time"])
        w.writerows(rows)
    headers = ["Badge ID", "First Name", "Last Name", "Email", "Phone", "Gender", "Department", "Job Position",
               "Job Role", "Shift", "Work Type", "Reporting Manager", "Employee Type", "Location", "Date Joining",
               "Basic Salary", "Salary Hour", "CTC", "Contract End Date", "Company"]
    with open(args.employees_out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(headers)
        for i, badge in enumerate(WEEKS, start=1):
            first, last = NAMES[badge].split(" ", 1)
            w.writerow([badge, first, last, f"{badge.lower()}@example.com", f"080000000{i:02d}", "male", DEPTS[badge],
                        "Farm Worker", "Farm Worker", "Day Shift", "Farm", "", "Casual", "Farm", "2024-01-01",
                        BASIC[badge], 0, "", "", args.company])
    print(f"{args.employees_out}: 4 test workers")
    print(f"{args.out}: {len(rows)} rows, 28 days from {start:%d/%m/%Y} to {start + timedelta(27):%d/%m/%Y}")
    print(f"Payroll run to test: start {start}, end {start + timedelta(27)}, working days 28 (24 for TST004).")


if __name__ == "__main__":
    main()
