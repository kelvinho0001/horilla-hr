# Demiller Farms HRM: setup, testing, employees and branding

Plain-language guide for the person running the system. Server steps are in `DEPLOYMENT.md`; day-to-day operation and roles are in
`docs/demiller/PRODUCTION_RUNBOOK.md`.

## Contents
1. Short answers to your questions
2. Employees and biometric IDs (import / sync)
3. What to put in place before real use (checklist, in order)
4. How to test (three levels) and the payroll rehearsal with expected numbers
5. Changing the login logo and favicon
6. What is not built yet

---

## 1. Short answers

| Question | Answer |
|---|---|
| How do we test it? | Three levels: (a) automated tests (already pass), (b) a **rehearsal** with 4 test workers whose correct answers are known (section 4), (c) a **parallel run**: do the first real weeks on paper/Excel *and* in the system and compare. |
| Do I import employees from Time.Net / BioTime? | **Not automatically yet.** Horilla is the master list. You add workers to Horilla (Excel import or one by one) and the biometric ID is matched to them when you upload the attendance report. A Time.Net/BioTime employee sync is a planned connector that needs the site checks in `SITE_CHECKLIST.md`. |
| How do we test payroll? | Section 4: load 4 test workers with the same numbers as your sample sheet. The system must produce **12,500.00 / 17,821.43 / 22,200.00 / 70,000.00 (total 122,521.43)**. |
| What must be set up? | Section 3. |
| Change the login logo and favicon? | Section 5: one command with your logo file, then `collectstatic` and restart. |

---

## 2. Employees and biometric IDs

### The rule
**Horilla is the master list of workers.** The devices (and Time.Net) only supply attendance punches and, at most, names. Nothing is
created in Horilla from a punch, and nothing in Horilla overwrites the device.

### Your devices (from the sample files)
| Device | Serial (in the file name) | Who | Pattern |
|---|---|---|---|
| Contract staff | `AEYU200860006` | casual/contract workers, IDs `SE###` | one punch per shift: night shift ~00:00-09:00 (mostly 04:00-05:00), afternoon shift ~14:00-20:00 |
| Admin staff | `AEYU200860012` | office staff (IDs `DF…`, `DMF…`, numbers) | in ~08:00, out ~18:00. **Not imported into Horilla for now** |

IDs are only unique **within** a device, so each device is its own source in Horilla (a file named `<serial>_attlog.dat` creates and
uses "Device <serial>" automatically; rename it in Django admin if you like).

### Getting attendance in (no Time.Net needed)
1. At the device: copy the attendance file to a USB drive (it is named `<serial>_attlog.dat`).
2. **Attendance > Biometric Import > Upload attendance**: choose the `.dat` file. (For a file you renamed, pick the device in the list.)
   Uploading the same file again, or a newer file that repeats old punches, is safe: only new punches are added.
3. **Attendance > Biometric Attendance**: choose the dates (any range, or whole months) and press **Add punches to Attendances**.
   This creates one ordinary Horilla attendance record per worker per day (waiting for validation). Run it again after each upload; it never touches existing records.
4. **Attendance > Attendances** (Horilla's own page): the **Attendance To Validate** tab lists them. Select and validate in bulk. You can also **Create** attendance by hand there.
   **Payroll counts validated days only** (including ones you added by hand).
5. Days with punches outside the attendance windows (or in two shifts) are not added; they appear as red dots on Biometric Attendance. Open the day and **Add to Attendances anyway** if it is genuine.
Each line of the device file is `ID, date-time` plus four device codes that attendance does not use (kept in the raw record).

### Putting the workers in Horilla (one time, then for new starters)
1. Copy `user.dat` (the user list) from the contract device to USB.
2. **Attendance > Biometric Import > Worker sheet**: upload `user.dat`. Optional: the employee list exported from Time.Net (CSV/Excel with an ID column, name columns and a Department column): it supplies full names and **the department** (the device has none).
   * Company: pick yours. Fill the defaults that apply to everyone (job position, role, worker category such as *Casual*, date joining).
   * **IDs to leave out:** the device's own admin and office staff (e.g. `1`, `104`).
3. You get `workers-to-import.xlsx` with three sheets:
   * **WORKERS**: exactly Horilla's import format. The device name `FIRST.LAST` is split into first and last name; the ID becomes the **Badge ID**; workers without an e-mail get a placeholder `id@workers.invalid` and a placeholder phone number (the system needs them; replace later if you want real ones). Fix names and fill in departments here.
   * **REVIEW**: things to check for each worker: same name twice (one person with two IDs?), same name as an existing employee, a name with no surname or an extra full stop, IDs with no punches yet, device privilege levels.
   * **NOT INCLUDED**: IDs left out or already in Horilla, **IDs that punched but are not on the device user list** (e.g. leftovers from deleted users or tests), and Time.Net employees not on this device.
4. **Employees > Import** with the WORKERS sheet. Horilla itself refuses a Badge ID that already exists, so running it twice cannot create duplicates.
5. Nothing is created until you do step 4.

### Linking IDs to workers
1. After an upload, open **Unmapped IDs**. A worker is suggested when the ID equals a Badge ID **exactly** (names are never used).
2. **Map all matching IDs** links every suggested one in one click. Earlier punches of those IDs attach to the worker too.
3. Anything else: **Map** one by one, or **Ignore** (with a reason) for IDs that are not payroll workers: admin staff, test punches, leftovers. Ignored IDs keep their punches but stop being listed and are not counted as unmapped.
4. One worker can have several IDs (map each). One ID belongs to one worker per device.

### New starters and leavers
* **New worker:** add in Horilla first (Badge ID = the ID on the device); enrol on the device and in Time.Net with the same ID; after the next upload, map.
* **Leaver:** set the Contract End Date (later punches are ignored) or archive the worker, and deactivate the mapping.

### Time.Net / BioTime connectors
Not needed for this workflow and not built (see section 6). The USB file is the supported path today; an automatic upload from the office PC can be added later.

---

## 3. What to put in place (do these in order)

Menu names can differ slightly by Horilla version.

| # | Where | What |
|---|---|---|
| 1 | Settings > Company | Company name (shown on the login page and tab title) and details. Mark it as the head-office company. |
| 2 | Run `python manage.py demiller_check` | 0 failures. Warnings: read them. |
| 3 | Biometric Import > Worker sheet, then Employees > Import | Workers with Badge ID = biometric ID (section 2). Departments and worker categories come with it. |
| 4 | Leave / Attendance > Holidays | Public holidays, and leave types if you use leave. Approved leave and holidays are not counted as "missing". |
| 5 | Settings > Roles and Permissions | Create logins for staff (they are Employees with an email) and add each to **one** group: **HR Officer**, **HR/Payroll Approver**, **Management**, **Accountant**, **IT Administrator**. Make sure the assignment is **for your company** (Horilla applies group rights per company). Keep the approver and Management groups small. |
| 6 | Attendance > Biometric Attendance > Attendance rules | A global rule with the **attendance windows**. A rule can have several windows. For the contract device use two: **00:00-09:00** (night shift) and **14:00-20:00** (afternoon shift). A punch outside both is flagged instead of added; a worker with a punch in both windows on one date is flagged too (nobody should work two shifts in a day). Add department rules only where they differ. With **no** rule any punch time counts. |
| 7 | Attendance > Biometric Import | Upload a real week, map IDs. |
| 8 | Payroll > Bank Export > Bank list | Already loaded (87 banks). Check `044` shows its zero. |
| 9 | Payroll > Bank Export > Worker bank accounts | Lists every employee with what their Employee > Bank details say and the bank it matched. Fix anyone not OK: pick the bank from the drop-down and enter the 10-digit account number. |
| 10 | Employee > Work information | **Basic Salary** (full month) on every worker. This is what payroll uses; there is no separate pay-profile screen. (The worker-sheet/import can fill it too.) |
| 11 | Payroll > Loans (Horilla's own page) | Loans, salary advances and fines are created here (tabs Loan / Salary Advance / Fine): amount, number of instalments, instalment start date. Payroll reads them. A fine with one instalment is taken in full by the first payroll run that ends on or after its date. |
| 13 | Server | HTTPS only (`SECURE_SSL_REDIRECT=True`), **nightly backup cron** (DEPLOYMENT.md section 10) and a copy of backups kept **off the server**, admin password changed, `TIME_ZONE=Africa/Lagos`. |
| 14 | Branding | Section 5. |

Who does what in a pay cycle: IT/HR uploads attendance > HR Officer adds the punches to Attendances and **validates** them (Horilla's page) >
HR Officer creates the payroll run (enters working days) and marks it reviewed > **Approver** approves payroll > **Accountant**
generates the bank file, uploads it to the bank, marks it paid. Fines, loans and salary advances are entered by HR in Horilla's Payroll > Loans.

---

## 4. Testing

### Level 1: automated tests (developers; already passing)
```bash
python manage.py test biometric_integration attendance_processing weekly_payroll bank_export
```
These cover duplicates, unmapped IDs, one-day-one-attendance, approvals and locking, the payroll formula, loan and fine rules,
roles, and the bank workbook. They use a throw-away database, so with SQLite they never touch live data; they take several
minutes. Prefer running them on a developer machine before each deployment. `weekly_payroll` includes a test that reproduces the
four-worker sample below.

### Level 2: the payroll rehearsal (you, in the browser)
**Before you start, protect your setup:** finish checklist items 1-9 first (company, roles, rules, bank list). Then copy the database so you can undo the rehearsal:
```bash
cp ~/hrm_data/horilla.sqlite3 ~/hrm_data/before-rehearsal.sqlite3
```
**To undo afterwards:** in Setup Python App **Stop** the app, `cp ~/hrm_data/before-rehearsal.sqlite3 ~/hrm_data/horilla.sqlite3`, **Start** it. (Approved payroll runs cannot be deleted, so always rehearse on a copy or restore like this.)

**1. Make the test files** (on the server, in the app folder with the virtualenv active):
```bash
python scripts/make_test_attendance.py --company "Demiller Farms"
```
This writes `test_employees.csv` (4 test workers TST001-TST004) and `test_attendance.csv` (28 days, in the past; it prints the start and end dates). The attendance file deliberately includes a **repeated punch**, an **unknown ID (TST999)** and a **bad date** row.

**2. Import the workers:** Employees > Import > `test_employees.csv`. Expect "4 imported".

**3. Upload attendance:** Biometric Import > Upload `test_attendance.csv`. Expect 79 rows imported, **1 error** (the bad date, with its row number) and **unmapped IDs** TST001-TST004 and TST999. Upload the **same file again**: expect **0 new** (all duplicates). (The file is not named `<serial>_attlog.dat`, so choose or create the device when asked, or rename it to e.g. `TESTDEVICE_attlog.dat` and upload it as a `.dat`: it then files itself under "Device TESTDEVICE".)

**4. Map IDs:** Unmapped IDs > **Map all matching IDs** (the four TST IDs equal their Badge IDs). Leave TST999 unmapped, or **Ignore** it with a reason.

**5. Add the punches to Attendances:** Attendance > Biometric Attendance > range = the 28 days printed in step 1 > **Add punches to Attendances**. Expect 77 records (15+19+19+24), all amber (waiting). Try "from month / to month" too. Click a day to see the raw punches. TST001's first day has two punches and is still **one** record.
* Open **Attendance > Attendances**: the same records are in **Attendance To Validate**. Select all and **validate** (log in as an HR Officer: the validate action is available to them). They turn green on Biometric Attendance.
* Try **Create** on Attendances: add one day by hand for TST004 and validate it: payroll will count it (it changes TST004's total, so delete it again before comparing with the table below).

**6. Basic salaries:** Employee > Work information: TST001 70,000, TST002 55,000, TST003 70,000, TST004 70,000. (The test import file already carries them in its Basic Salary column; check they are there.)

**7. Fines:** Payroll > Loans > **Fine** tab > Create: TST001 5,000, TST002 4,500, TST003 2,300, **1 instalment**, start date inside the 28 days.

**8. Loans:** Payroll > Loans > **Loan** tab > Create: TST001 20,000, TST002 15,000, TST003 23,000, **1 instalment**, start date inside the 28 days (a Salary Advance in the **Salary Advance** tab counts in the IOU column the same way).

**9. Payroll run:** Weekly Payroll > New run: start and end = the 28 days, **default working days 28**. Set **TST004 to 24** (working days column). Expected result:

| Worker | Present days (Wk1-4) | Working days | Basic | Fines | IOU | **Amount earned** |
|---|---|---|---:|---:|---:|---:|
| TST001 | 15 (3,5,6,1) | 28 | 70,000 | 5,000 | 20,000 | **12,500.00** |
| TST002 | 19 (4,5,4,6) | 28 | 55,000 | 4,500 | 15,000 | **17,821.43** |
| TST003 | 19 (6,2,5,6) | 28 | 70,000 | 2,300 | 23,000 | **22,200.00** |
| TST004 | 24 (6,6,6,6) | 24 | 70,000 | 0 | 0 | **70,000.00** |
| | | | | | | **Total 122,521.43** |

Formula: `ROUND(days ÷ working days × basic, 2) − fines − IOU`. TST002: 19 ÷ 28 × 55,000 = 37,321.43 − 4,500 − 15,000 = 17,821.43.

**10. Check the controls:**
* Download Excel now: file name has **-DRAFT**, RUN INFO says NOT APPROVED. Open it in Excel: the numbers recalculate to the same values.
* Change one worker's working days: the amount changes and a "reviewed" run goes back to draft.
* **Mark reviewed** (HR Officer), then try **Approve** as HR Officer: refused. Approve as **Approver**: the run locks, and the fines/loans become **Settled** in Payroll > Loans.
* Make a **second run** over the same days: it shows **no fines and no IOU** (already deducted) and cannot be approved while overlapping the first.
* A run that ends **before** a loan's instalment date shows IOU 0 (monthly instalments are only taken when due).
* Remove a worker's basic salary on the employee record and press **Recalculate**: the line is flagged and **Mark reviewed** is refused until it is fixed.

**11. Bank export:** Payroll > Bank Export > Worker bank accounts lists every employee with what their record says. Give TST001-TST003 a bank and a test account number (e.g. `0123456789`, `0000000042`: keep the leading zero case), leave TST004 without. As **Accountant**: Bank Export > the run. Expect **TST004 listed as a problem** and the export blocked; tick "export valid workers only" (or add the account) and generate. Open the file: **PAYMENT SCHEDULE** has the right bank codes, account numbers with their leading zeros, amounts, and the narration; the count/total equal the payroll (excluding the left-out worker). A second export is refused; **Supersede** (Approver, reason) lets you regenerate; **Mark paid** is final.

**12. Undo the rehearsal** (restore the copy as described above).

### Level 3: parallel run with real data (before relying on it)
Run your first **two real pay cycles both ways**: your normal Excel process *and* the system, then compare worker by worker. Differences are
usually wrong IDs, missing holidays/leave, wrong working days or an attendance window that does not match reality. Only stop the manual process after
two cycles match and the accountant has uploaded a generated file to the bank successfully. Use `UAT_CHECKLIST.md` to record results.

---

## 5. Login logo and favicon

The login page, error pages and payslip PDFs take the logo from **`static/images/ui/auth-logo.png`**; e-mails sent by the system use **`static/images/ui/horilla-sticker-round.png`**; the browser-tab icon comes from **`static/favicons/`**.
Replacing those files is the reliable method on cPanel (uploading a logo in Horilla's company screen stores it in `media/`, which Django does not serve there).

**You need:** your logo as a **PNG** (transparent background if possible, at least 512 px on the longer side). Optionally a separate **square** icon for the tab.

On the server (File Manager can upload `logo.png` to your home folder):
```bash
source /home/demiyvrf/virtualenv/hrm.demillerindustries.com/3.12/bin/activate
cd ~/hrm.demillerindustries.com
git pull                                  # gets scripts/apply_branding.py and WHITE_LABELLING
python scripts/apply_branding.py ~/logo.png          # add:  --favicon ~/icon.png   for a separate tab icon
python manage.py collectstatic --noinput
```
Then **Restart** the app in Setup Python App and hard-refresh the browser (**Ctrl+F5**; browsers cache favicons for a long time. If the old icon persists, open the site in a private window).

What the script writes: `auth-logo.png` (fitted to 400x140, transparent), `horilla-sticker-round.png` (e-mail logo, 900x900), `favicon-16x16.png`, `favicon-32x32.png`, `apple-touch-icon.png` (180),
`android-chrome-192x192.png`, `android-chrome-512x512.png`, `favicon.ico`.

**Name instead of "Horilla":** `WHITE_LABELLING = True` is set in `horilla/settings/local_settings.py`, so the tab title, login page and emails use your **company name** (from Settings > Company, head-office company), and no longer say "Horilla". Restart after pulling.

**Future updates:** if `git pull` complains about changed files in `static/`, run `git checkout -- static/images static/favicons`, pull, then run the branding steps again. (Better: run the script once on your own computer, commit the changed files, and pull on the server.)

---

## 6. What is not built yet

* **Time.Net / BioTime / direct-device connectors.** Needs the on-site facts in `SITE_CHECKLIST.md`. Until then the supported path is uploading the device's `.dat` file (or CSV/Excel), which works today.
* **Automatic bank payment.** The system only produces the Excel file.
* **An "un-approve payroll" button.** Approved payroll is locked on purpose (see the runbook for how corrections are handled).
* **Horilla's own background jobs on cPanel** (see DEPLOYMENT.md section 11).
* Nothing here has been checked against your real bank's upload rules: the accountant should confirm the generated **PAYMENT SCHEDULE** is accepted by the bank before relying on it.
