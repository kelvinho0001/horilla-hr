# Demiller Farms HRM — Phase 0 Technical Discovery Report

Status: **Draft for review. No Horilla core code has been modified.**
Basis: static inspection of this repository at commit `56f0163` (Horilla `2.3.1`, branch `2.0`).
Not inspected: the Time.Net 3.0 installation, the uFace 202 device, a BioTime server, the bank template. Those are listed as open items in section 7. Anything not read from the code is labelled **UNVERIFIED**.

---

## 1. Horilla architecture and existing modules

### 1.1 Version and stack
| Item | Finding |
|---|---|
| Product version | `2.3.1` (`horilla/__version__.py`, CHANGELOG dated 2026-11-08). Default branch here is `2.0`. |
| Django | `~=5.2.17` |
| Python | 3.12 – 3.14 (spaCy caps at <3.15). Sandbox has 3.13. |
| DB | PostgreSQL (`psycopg2-binary`); docker-compose provided. SQLite is possible for dev. |
| Scheduler | `django-apscheduler` + `horilla.scheduling.register_job` (in-process interval jobs). No Celery. |
| Audit | `django-auditlog` and `django-simple-history` (`HorillaAuditLog`) available. |
| Excel | `openpyxl 3.1.5`, `pandas`, `django-import-export` already present. |
| ZK library | `pyzk==0.9` (already used by `biometric/`). |
| API | DRF + SimpleJWT in `horilla_api/` (attendance, payroll, employee, … views). |
| Tests | Django test runner; `make test-smoke` over all first-party apps; payroll has golden tests. |
| Remotes | Only `origin` (kelvinho0001/horilla-hr). **`upstream` is not configured** (brief §17 requires it). |

### 1.2 Existing biometric support (`biometric/`)
- `BiometricDevices` (UUID pk): types `zk`, `anviz`, `cosec`, `dahua`, `etimeoffice`. Stores IP/port/ZK password/API creds, `is_scheduler`, `scheduler_duration`, `last_fetch_date/time`, direction.
- `BiometricEmployees`: `employee_id` FK, `device_id` FK, `user_id` (char), `uid`, `ref_user_id`. **No unique constraint** on `(device_id, user_id)`, so one biometric ID can currently map to several employees. Brief §4.1 forbids that.
- ZK flow (`biometric/views.py:2171 zk_biometric_attendance_logs`): connects with `pyzk`, pulls all logs, filters by `last_fetch_*`, maps `(device, user_id)` through `BiometricEmployees`, then **calls `clock_in` / `clock_out` directly** to build `AttendanceActivity` / `Attendance`.
- Polling: `poll_biometric_devices` runs every minute through `register_job`.

**Gap that drives the design:** Horilla keeps **no raw transaction table**. Unmapped punches are silently dropped. The watermark is a date/time on the device row, so late-arriving records are lost. There is no batch, no dedup key and no import history. Brief §4.2, §9 and §15 require all of these, so the raw layer must be new.
There is also no concept of a provider other than a physical device (no BioTime, no Time.Net, no file import).

### 1.3 Existing attendance (`attendance/`)
- `Attendance` (`unique_together = (employee_id, attendance_date)`): clock-in/out fields, worked hours, validation and approval flags. It is built around in/out and hours.
- `AttendanceActivity`: individual punches. Not a raw biometric audit table; it is derived from `clock_in`/`clock_out`.
- `WorkRecords`: per-employee, per-day status `FDP` / `HDP` / `ABS` / `HD` / `CONF` / `DFT`, with leave and attendance links and `day_percentage`. **This is the closest existing "daily presence" record.**
- Also present: `AttendanceValidationCondition`, `GraceTime`, `AttendanceGeneralSetting`, `AttendanceSummaryOverride`, `AttendanceConflictResolution`, `AttendanceLateComeEarlyOut`.
- Shifts and work schedules come from `base` (`EmployeeShift`, `EmployeeShiftSchedule`). Holidays and company leave are in `base` / `leave`.

Reuse assessment: the once-a-day rule and the "valid window per department" rule do not exist. Horilla's model assumes in/out. Rather than bend `Attendance` (core), the plan is a **new daily-presence layer that reads the raw transactions and writes its own approved record**, and optionally mirrors to `WorkRecords` / `Attendance` for display compatibility (decision D2 below).

### 1.4 Existing payroll (`payroll/`)
- `Contract`: `wage_type` ∈ {daily, monthly, hourly}, `pay_frequency` ∈ {**weekly**, monthly, semi_monthly}, `wage`, `hourly_wage`. Daily and weekly wage types are therefore **already supported at the contract level**.
- `Allowance`, `Deduction`, `SalaryStructure`, `Payslip` (status draft / review_ongoing / confirmed / paid; `pay_head_data` JSON; `installment_ids` M2M to `Deduction`), `Reimbursement`.
- `PayrollBatch` (draft / review / approved / paid / cancelled, with an enforced `ALLOWED_TRANSITIONS` state machine; payslip status locked while the batch is approved or paid) and `PayrollBatchLine` (unique per batch + employee).
- `PayPeriodSettings`: pay periods are **calendar month or a day-of-month cycle only. There is no weekly option.** A weekly period can still be created as a batch with arbitrary `period_start` / `period_end`, but the proration helpers (`months_between_range`, LOP divisors) are month-based and need auditing for 7-day periods. Existing `tax_calc.py` already knows `"weekly": 52`.
- `compute_salary_on_period(...)` in `payroll/methods/methods.py:747` is the engine entry point. `calculate_based_on_attendance` and `get_attendance` (methods.py:171) pull attendance from `WorkRecords` / `Attendance`.
- A large payroll test suite, including golden tests, exists. It must stay green.

### 1.5 Loans, advances and fines
- `LoanAccount` (payroll): type ∈ {loan, advanced_salary, fine}. It has `installments` (count), `installment_start_date`, `settled`, and generates `Deduction` rows. `get_installments()` steps by calendar **month** (`get_nth_month_same_date`); `apply_on` is hard-coded `end_of_month`. It uses `FloatField` for money. There is **no repayment-frequency field, no outstanding-balance field, no repayment ledger and no approval fields**.
- `PenaltyAccounts` (base): created from late-come/early-out or leave penalties or manually. It has `penalty_amount` but **no approval workflow, no waiver and no payroll-period link**. Late/early penalties auto-generate from attendance, which conflicts with brief §8 ("do not automatically impose fines").
- Consequence: the "monthly instalment on a weekly payroll" rule (brief §7) **cannot be met by LoanAccount as is**. Its installment dates are month-anchored and nothing records what was deducted against which run. Float money also conflicts with the decimal requirement.

### 1.6 Bank details, exports, permissions
- `EmployeeBankDetails` (employee, OneToOne): `bank_name`, `account_number` (CharField, so leading zeros are safe), `any_other_code1` (bank code). Reusable as is; it needs validation rules, not a new table.
- Excel export exists generically (openpyxl). There is no bank-payment export.
- Permissions: standard Django permissions with `permission_required` decorators plus Horilla's own `login_required` / manager helpers. Roles in brief §12 can be built as Django Groups with custom permissions in the new apps; no core change needed.

---

## 2. Time.Net 3.0 findings (UNVERIFIED — no access to the installation)

I could not inspect the installed application. The items below are the questions that must be answered on the Time.Net machine before any connector is written. I have deliberately not asserted a database engine or an API.

| Brief §3.2 question | Status |
|---|---|
| Installed exact version/build | Unknown. Check Help → About. |
| Database engine and location | Unknown. Check the install folder and the DB configuration/connection tool; look for `.mdb`/`.sqlite`/SQL Server instance/MySQL service in Windows Services. |
| Public API | Unknown. Do not assume one. |
| ZKTeco SDK | ZKTeco publishes a "Standalone SDK" for direct device communication. Licensing and uFace 202 support are **unverified**. |
| Scheduled export | Unknown. Look for a report scheduler, auto-export or "auto download" settings in Time.Net. |
| Attendance table structure | Unknown. Needs a read-only inspection, never a write. |

**Investigation order:** (1) device direct via pyzk, (2) Time.Net scheduled export, (3) read-only DB, (4) Windows bridge, (5) manual CSV/XLSX upload. The manual upload is the first connector I will build, since it needs no unknowns.

### Direct device (uFace 202)
Horilla already speaks ZK protocol through `pyzk` on a LAN IP and port (typically 4370) with an optional numeric comm key. **Whether the uFace 202 firmware answers pyzk is UNVERIFIED** and is the cheapest test: run `ZK(ip, port=4370).connect(); get_attendance()` from a machine on the same LAN. If it works, the bridge and Time.Net DB access may be unnecessary. Caveat: if Time.Net is also pulling from the device, two consumers on one device is generally fine for log reads but should be confirmed, and the device must not be cleared by either side.

### BioTime
Brief lists BioTime 8.5 endpoints (`/personnel/api/employees/`, `/iclock/api/transactions/`). I have **not** read the official PDF (not accessible in this sandbox). The connector should be written against the documentation for the installed version, with endpoints and auth in config, not code.

---

## 3. Recommended architecture

Provider-based, and independent of payroll. Horilla core is untouched except for settings registration.

```
Providers (ManualFile | DirectZK | TimeNet* | BioTime | future)
        │  each declares capabilities; returns NormalizedTransaction
        ▼
biometric_integration.RawTransaction   (immutable audit table)
        │  employee_mapping service
        ▼
attendance_processing.DailyAttendance  (one row per employee/day, derived)
        │  HR review / exceptions / corrections (audited)
        ▼
attendance_processing.AttendancePeriod (approve → lock; reopen with reason)
        │  approved days only
        ▼
weekly_payroll  → Horilla PayrollBatch / Payslip (reused)
        ▼
bank_export (Excel from approved batch only)
```
\* Time.Net connector type decided after discovery.

Key rules baked in:
1. Raw rows are append-only and never edited. Uniqueness: `(provider, source_account, external_transaction_id)`, falling back to a hash of `(provider, device, external_employee_id, timestamp)` when no stable ID exists.
2. Daily attendance is *derived*; multiple punches on a day collapse into one Present, with all raw rows linked.
3. Payroll reads only approved `DailyAttendance`. Swapping provider changes nothing downstream.
4. A successful sync never triggers payroll.

---

## 4. Proposed database changes

New tables only (all money fields `DecimalField`; all models inherit `HorillaModel` for company scoping and audit).

**biometric_integration**
- `BiometricProviderConfig`: provider type, name, base URL / path, credentials (encrypted or env-referenced, never committed), capabilities, active, `source_timezone`.
- `BiometricEmployeeMapping`: employee FK, provider config FK, `external_employee_id`, `external_employee_code`, `device` (nullable), `external_device_user_id`, status (verified / suggested / conflict), active, `last_synced_at`. **Unique `(provider_config, external_employee_id)` where active.** One employee may hold many mappings. Changes are audited. This replaces nothing; it can *seed from* existing `BiometricEmployees` and `Employee.badge_id`.
- `ImportBatch`: provider, started/finished, period covered, counts (received / imported / duplicate / unmapped / failed), status, error text, trigger (auto/manual/file), file name+hash.
- `RawTransaction`: fields per brief §9, plus `dedup_key` (unique), `mapping` FK (nullable), `processing_status`, `error`. No fingerprint templates are ever stored.

**attendance_processing**
- `AttendanceRule`: scope (department / shift / worker category / employee, most-specific wins), valid window start/end, working weekdays, review policy for out-of-window punches.
- `DailyAttendance`: unique `(employee, date)`; status (Present / Absent / Leave / Holiday / Rest / Review), source (biometric / HR correction), raw transaction count, flags, `period` FK.
- `AttendanceCorrection`: before/after, mandatory reason, user, timestamp.
- `AttendancePeriod`: start/end, status (open / approved / reopened), approver, `ReopenLog` (reason, user).

**weekly_payroll** (extends, does not fork, Horilla payroll)
- `WorkerPayProfile`: employee, pay basis (daily / weekly fixed), rate, effective dates, approval. Complements `Contract` where Contract cannot express per-employee daily rates cleanly (decision D3).
- `PayAdjustment`: employee, period, kind (earning / deduction), description, `Decimal` amount, approval status, authoriser.
- `LoanAgreement` (extension, not a replacement of `LoanAccount`): one-to-one to `LoanAccount`, repayment frequency (weekly / monthly), instalment `Decimal`, outstanding balance, status. `LoanRepayment` ledger: loan, payroll batch line, amount, unique `(loan, batch_line)` so a re-run cannot double-deduct.
- `Fine`: approval, waiver / reversal, target period. Kept separate from attendance. Not auto-generated. Can reuse `PenaltyAccounts` only after the late-penalty auto path is confirmed off for these workers (decision D4).

**bank_export**
- `BankExportTemplate`: ordered column mapping, configurable.
- `BankExportBatch`: payroll batch FK, reference, totals, user, file, status. Unique active export per payroll batch unless explicitly superseded with a reason.

---

## 5. Proposed Django apps and files

```
biometric_integration/   connectors/{base,manual_file,direct_zkteco,biotime,timenet}.py
                         services/{employee_sync,employee_mapping,attendance_sync,
                                   transaction_normalizer,duplicate_detection}.py
                         models.py views.py urls.py tasks.py tests/
attendance_processing/   models.py services.py calculators.py views.py urls.py tests/
weekly_payroll/          models.py services.py loans.py views.py urls.py tests/
bank_export/             models.py exporters.py services.py views.py urls.py tests/
docs/demiller/           this report, deployment and rollback notes
```
Core touch points (minimal): add the four apps to `INSTALLED_APPS` via `horilla/settings/addons.py` if it supports that, plus sidebar/menu registration. Existing `biometric/` is **not modified**; its device-config UI stays. The new framework can reuse `BiometricDevices` rows as the "DirectZK" provider source.

---

## 6. Implementation phases (revised)

| Phase | Work | Exit criteria |
|---|---|---|
| 0 | This report; on-site Time.Net + device checks; collect bank template and sample exports | Open items in §7 answered |
| 1 | Add `upstream` remote, dev environment, baseline test run, create departments / worker categories / sample casual workers | `make test-smoke` green on fresh DB |
| 2 | `biometric_integration`: models, connector base, **manual CSV/XLSX connector first**, mapping UI, batches and history, dedup. Then direct ZK. Then BioTime and Time.Net as discovery allows | Same file imported twice gives zero new rows |
| 3 | `attendance_processing`: rules, daily collation, exceptions, review UI, approval and lock | Approved week is immutable without audited reopen |
| 4 | `weekly_payroll`: pay profiles, 7-day batch, loan ledger, fines, adjustments, approval | Golden weekly tests; no double deduction on recalculation |
| 5 | `bank_export` against the supplied template | Totals reconcile; leading zeros kept |
| 6 | Deployment, backups, monitoring, UAT, rollback | Signed UAT |

---

## 7. Open decisions and information required

### Decisions I need from you (my recommendation in bold)
- **D1. Pay week definition:** Mon–Sun or Sun–Sat, and is Sunday only the processing day? *Recommend Mon–Sun with Sunday as processing day, configurable.*
- **D2. Where approved daily attendance lives:** new `DailyAttendance` table that payroll reads directly (**recommended; keeps core untouched**), or mirror into Horilla `WorkRecords` / `Attendance` so existing Horilla screens and the stock payroll engine see it. Mirroring is a thin optional adapter.
- **D3. Wage calculation:** extend `Contract` (daily wage_type exists) or use a new `WorkerPayProfile`. *Recommend profile for per-employee daily rates, feeding Horilla `Payslip` so payslips and batch states are reused.*
- **D4. Fines:** confirm late-come/early-out auto-penalties are switched off for casual workers.
- **D5. Loans:** OK to extend `LoanAccount` via a companion `LoanAgreement` and decimal ledger rather than editing the float model?
- **D6. Payroll status mapping:** Horilla batch states are draft / review / approved / paid. Brief also lists "Exported". *Recommend tracking Exported on `BankExportBatch`, leaving the core state machine as is.*

### Business rules (brief §18)
Working days; whether all departments share one attendance window; handling of multiple punches (recommend: count one day, flag when outside the window); holiday and rest-day pay; overtime rules; fine deduction timing; who approves attendance and payroll.

### Information needed from site
1. Time.Net version, database engine, install path and DB location. Screenshots of Help → About and any database setup tool.
2. A sample weekly attendance export from Time.Net (CSV/XLS), with names redacted if preferred.
3. Device model/firmware, its LAN IP and comm key, and whether the Horilla server can reach that LAN (or whether a bridge is needed).
4. Whether a BioTime server is planned, and its version.
5. The accountant's bank upload template (the actual file) and the bank name.
6. Employee list with biometric IDs, department, category, daily rate, bank details; open loans with their agreed frequency and instalment.

### Risks and limitations
- uFace 202 / Time.Net compatibility with pyzk and any DB access is unverified.
- Horilla `LoanAccount` uses floats and monthly dates; our ledger must own the money maths.
- 7-day pay periods are not a first-class `PayPeriodSettings` option; period proration needs auditing, though payroll over arbitrary ranges is possible.
- Without an `upstream` remote, merges from official Horilla can't be tracked; to be added in Phase 1.
- Biometric data covers personal data; NDPR/NDPA handling and retention need to be agreed with management.

---

## 8. Decisions received and manual payroll template analysis

### 8.1 Decisions (supersede the recommendations in §7)
| ID | Decision |
|---|---|
| D1 | The pay week or day does not matter. Payroll must be generated for **any date range on demand**. No fixed period is hard-coded. |
| D2 | Approved daily attendance lives in a **new table** (`DailyAttendance`) that payroll reads directly. |
| D3 | Payroll output is **the existing manual Excel sheet**, pre-filled by Horilla and the biometric data (see 8.2). |
| D5 | `LoanAccount` is **extended**, not replaced. |
| D4 | Late/early penalties are **not** switched off in code. Horilla serves both admin and regular staff. HR must be able to create fines, set amounts and assign them to employees. |
| Import | Manual attendance report upload is accepted; it is the first connector. |

### 8.2 `PAYROLL_TEMPLATE.xlsx` (Sheet1, Excel table `Table1`)
Columns: `EMP ID | FIRST NAME | LAST NAME | WEEK 1..4 | TOTAL DAY | WORKING DAYS | BASIC SALARY | FINES | IOU | AMOUNT EARNED`.
- `TOTAL DAY` = WEEK 1 + WEEK 2 + WEEK 3 + WEEK 4 (days attended).
- `WORKING DAYS` is typed per employee (28, 28, 28, 24).
- `AMOUNT EARNED` = `WORKING DAYS / TOTAL DAY * BASIC SALARY - FINES - IOU`.
- `IOU` is the loan or advance deduction. `FINES` is a flat amount.
- No bank columns. The bank upload template is still needed (Phase 5).

**Possible error in the template formula.** As written it divides working days by days attended. Row 2: 15 days attended, 28 working days, 70,000 basic gives 28/15 x 70,000 - 5,000 - 20,000 = **105,667**, more than the basic salary. The reverse (15/28 x 70,000 = 37,500) looks intended. Rows 2-4 would be overpaid; row 5 (24/24) is unaffected, which may be why it went unnoticed. This must be confirmed before the formula is reproduced.

### 8.3 Design consequences
- Payroll sheet = a **period report**, not a fixed 4-week month. WEEK columns are generated from the chosen date range (consecutive 7-day buckets from the start date; a short final bucket is allowed). Any range is accepted.
- Pre-filled by Horilla: EMP ID (badge id), names, week counts and TOTAL DAY from approved `DailyAttendance`, BASIC SALARY from the pay profile, FINES from approved fines due in the range, IOU from the loan ledger. `WORKING DAYS` is calculated from the schedule, holidays and rest days, and is HR-overridable per row.
- Export keeps live Excel formulas and the `Table1` structure so the accountant's sheet looks and behaves the same. Computed values are also stored server-side so the figures are auditable and cannot change silently.
- Basic salary here is a **period amount (monthly)**, not a daily rate. The pay profile therefore needs a "basic for the period" amount plus the working-days divisor, with a daily-rate mode as an option.
- **Fines:** `LoanAccount` already has type `fine`, but there is no catalogue, approval or waiver. Add a `FineType` list (name, default amount) and an approval and waiver workflow on the extended loan/fine record. HR creates, amounts and assigns them. Nothing is created automatically from attendance, and existing Horilla late/early penalties remain untouched.
- `LoanAccount` extension: repayment frequency (weekly or monthly), `Decimal` instalment and balance, repayment ledger unique per loan and run, approval. Because the range is arbitrary, a monthly instalment is deducted once per calendar month in which it falls due, and exactly one run in that month takes it.

### 8.4 Open questions
1. Confirm the `AMOUNT EARNED` formula direction (8.2).
2. Is BASIC SALARY always the full-month amount, even when the report range is shorter than a month? Should a short range be prorated?
3. If a monthly loan instalment falls due and several overlapping ranges are exported, which run owns the deduction? Proposal: the first run that is marked final or approved.
4. What are the "WORKING DAYS" rules (28 vs 24 in the sample)? By department, by schedule, or typed by HR?
5. Fines: deducted in full in the first run after approval, or in instalments?
6. Bank upload template and bank name.
