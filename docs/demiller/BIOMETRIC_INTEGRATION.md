# biometric_integration (Phase 2)

Vendor-neutral framework: connector -> normalised transactions -> employee mapping -> `RawTransaction`.
Nothing here touches payroll or Horilla's `Attendance`; Phase 3 reads `RawTransaction`.

## Use
Attendance > **Biometric Import** (`/biometric-integration/`): upload a CSV/XLSX report, review import history,
fix **Unmapped IDs**, manage **Mappings**. Providers are created in Django admin; "Manual upload" is created automatically.

## Device file (`<serial>_attlog.dat`) and user list (`user.dat`)
The USB attendance file is tab-separated, no header: `ID<TAB>YYYY-MM-DD HH:MM:SS<TAB>4 device codes`; the serial in the file name selects the device (provider). `user.dat` (72-byte user records) feeds **Worker sheet**, which builds the Excel file for Horilla's Employees > Import. Unmapped IDs can be mapped in bulk (exact Badge ID match) or ignored with a reason.

## Report format (manual upload)
Header row required. Employee ID column (e.g. `Emp ID`, `User ID`, `Emp Code`) plus either one date-time column or separate
`Date` + `Time` columns. Dates are day-first (dd/mm/yyyy) or ISO. Other columns are kept in `original_data`.
Column names can be overridden in `ProviderConfig.config = {"columns": {"employee_id": "Badge"}}`.

## Guarantees (covered by tests)
- Re-importing a file, late/older records, or duplicates inside one file never create extra rows (`dedup_key` unique).
- All punches are kept; unmapped, bad-date and future-dated rows are reported on the batch, not dropped silently.
- One active mapping per (provider, biometric ID); one employee may have many; changes are audit-logged; matching is by
  exact `badge_id` suggestion only, never by name.
- Timestamps without a zone use `ProviderConfig.source_timezone` (default Africa/Lagos).
- No fingerprint/face data is stored.

## Adding a vendor
Subclass `connectors.base.BaseConnector`, declare `capabilities`, register in `connectors/registry.py`.
`direct_zkteco` (pyzk, read-only, never clears the device) exists but is **unverified against a uFace 202**.
BioTime and Time.Net connectors are pending site discovery.

## Core changes
Only `horilla/settings/local_settings.py` (adds the app). Run tests: `python manage.py test biometric_integration`.
