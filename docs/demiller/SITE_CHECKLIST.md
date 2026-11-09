# On-site checklist: Time.Net and the uFace 202

Everything below is **read-only**. Do not change settings, clear logs, or write to the Time.Net database.
Send the results (or screenshots) back; the connector and any Windows bridge are built from them.

## 1. Time.Net 3.0 (on the PC where it is installed)
1. Open Time.Net > Help > About: note the **version/build**.
2. Find the install folder (right-click the desktop shortcut > Open file location) and the data folder. Note any files ending
   `.mdb`, `.accdb`, `.db`, `.sqlite`, `.sdf`, `.bak`.
3. Is a database service running? Windows > Services: look for *SQL Server*, *MySQL*, *PostgreSQL*, *Firebird*. Note the names.
4. In Time.Net look for: scheduled/automatic download, auto-export, "Export" or "Report > Export to Excel/CSV", any API or SDK menu.
5. Export one **real weekly attendance report** (CSV/Excel) and send it (names may be masked, keep the ID column).
6. Note how a worker appears in the report (employee number vs device user ID).

Optional (PowerShell, read-only) to list candidates:

```powershell
Get-ChildItem "C:\Program Files*","C:\ProgramData","$env:APPDATA" -Recurse -ErrorAction SilentlyContinue `
  -Include *.mdb,*.accdb,*.sqlite,*.db,*.sdf,*.bak | Select FullName,Length,LastWriteTime
Get-Service | Where-Object { $_.Name -match 'SQL|MySQL|Postgre|Firebird|ZK' } | Select Name,Status
```

## 2. The device (uFace 202)
1. Device menu > Comm: note **IP address**, **port** (usually 4370), **communication key/password**.
2. From a computer on the same network: `ping <ip>`.
3. Run the probe (needs Python): `pip install pyzk` then `python scripts/zk_probe.py <ip> [--password <key>]`.
   * `RESULT: direct ZK connection works` -> the existing direct connector can be used (the server must be able to reach that network).
   * `RESULT: FAILED - ...` -> send the message; the device may use a different protocol or firmware.
4. Note whether Time.Net and the device are on the same LAN as the office PC and whether the hosting server can reach it (usually **not**: cloud servers cannot reach a private IP).

## 3. Decision after the checks
| Finding | Path |
|---|---|
| Time.Net can auto-export CSV/Excel to a folder | keep manual upload now; later a small Windows task uploads the file over HTTPS |
| Direct probe works but server cannot reach the LAN | a small Windows bridge on the office PC reads the device and posts to Horilla over HTTPS |
| Time.Net database is readable (and engine known) | read-only bridge query on the office PC (never write, never expose the DB to the internet) |
| None of these | keep using manual upload of the weekly report (works today) |

Do not build or install anything on the office PC until the MD approves the chosen path.
