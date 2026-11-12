# Production runbook

## Roles (assign per company)
| Group | For | Key powers |
|---|---|---|
| IT Administrator | whoever runs the biometric import | import reports, providers, mappings |
| HR Officer | HR staff | add punches to Attendances and validate, create payroll runs, mark reviewed, propose pay profiles/fines/loans, edit worker bank accounts |
| HR/Payroll Approver | one or two senior HR people | **approve payroll**, supersede an export, edit attendance rules |
| Management | MD / directors | view payroll, loans and attendance |
| Accountant | accounts | bank list, generate/download payment file, mark paid |

Horilla applies a group only in a company where the user holds a **company group assignment**; assign groups on the user's role
screen for the right company. `python manage.py test bank_export.tests.test_roles_matrix` proves the sensitive permissions sit with
the intended roles; re-run it after changing permissions. Keep the approver group small. A person who proposes should not be the one who approves where you can avoid it.

## Daily / weekly / monthly
* **Daily**: backup email/cron OK. **Weekly**: `python manage.py demiller_check`; disk space; look at Biometric Import > history for failed or unmapped rows.
* **Each pay run**: upload attendance -> Add punches to Attendances -> validate in Attendances -> create payroll run (working days) -> review -> approve -> generate payment file -> mark paid.
* **Quarterly**: restore a backup into a scratch database and open it; review who holds approver/Management/Accountant groups; remove leavers.

## Monitoring
* Uptime monitor (e.g. UptimeRobot) on `https://<host>/health/` (returns `{"status":"ok"}`).
* Cron emails (`MAILTO`) for failed backups; the script exits non-zero and prints the reason.
* `demiller_check` weekly (fails on missing migrations, wrong DEBUG/host settings, missing roles).
* App error log (path shown in Setup Python App) after each deploy.

## Backup and restore
Backup: `scripts/backup.sh` (database dump + media, 30-day retention). Copy off-server.
Restore into an **empty** database (never over live data without a fresh backup first):

```bash
gunzip -c db-YYYYmmdd-HHMMSS.sql.gz | psql "$DATABASE_URL"
tar -xzf media-YYYYmmdd-HHMMSS.tar.gz -C ~/horilla
python manage.py demiller_check
```

## Upgrading (including upstream Horilla)
Remotes: `origin` = Demiller repo, `upstream` = official Horilla. Never deploy upstream commits directly.
1. `git fetch upstream` and read the release notes/CHANGELOG; list new migrations (`git diff HEAD..upstream/<ref> --stat -- '*/migrations/*'`).
2. Create a branch `staging/upstream-<version>`; merge upstream; resolve conflicts (our code is in four separate apps plus one line in `horilla/settings/local_settings.py`, `passenger_wsgi.py`, `DEPLOYMENT.md`).
3. On a **staging copy** (restored backup): `migrate`, `demiller_check`, run `python manage.py test biometric_integration attendance_processing weekly_payroll bank_export payroll attendance`, then the UAT checklist's payroll and export cases with a copy of last month's data.
4. Backup production, tag, deploy (DEPLOYMENT.md section 12), verify, keep the previous tag for rollback.

## Rollback
Code: previous tag + restart. If migrations ran: restore the pre-update database backup too. Practise this once on staging.

## Corrections after approval
Approved payroll is locked on purpose: there is no "un-approve" button. If a mistake is found **before** a payment file is generated, ask
IT to cancel it at database level under the MD's written approval (not provided as a feature). If found after payment: correct it in the next
run with a fine/loan adjustment (with reason) and note it. For attendance, validated records can be edited on Horilla's Attendances page (Horilla records who changed them); payroll only counts what is validated when a run is calculated, and an approved run is a fixed snapshot. A generated payment file can be **superseded** (reason, Approver) until it is marked paid.

## Data protection (Nigeria: NDPA 2023 / NDPR)
* Only attendance metadata is imported; **no fingerprint/face data** is stored in Horilla. Keep it that way.
* Bank details and payroll are restricted by role; account numbers are masked in lists; payment files are downloadable only through the permission-checked page, never from a public folder.
* Keep the server HTTPS-only, keep secrets out of Git, rotate the admin password, remove accounts of leavers promptly.
* Decide and document retention (e.g. raw punches vs payroll records), who may export data, and how a data-subject request or breach is handled; register/notify as the law requires. This is a technical note, not legal advice: confirm with counsel.

## Known limits
* The Windows attendance bridge, BioTime and Time.Net connectors are **not built**: they need the site facts in `SITE_CHECKLIST.md`. Manual CSV/Excel upload is the supported path today.
* No automatic bank payment; the app produces the workbook only.
* Horilla's scheduler-based features do not run on cPanel (see DEPLOYMENT.md section 11).
