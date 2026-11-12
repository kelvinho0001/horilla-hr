"""
Build the Excel sheet for Horilla's own Employees > Import from the device's user list (``user.dat``).

The device stores each user as ID + one name field; ours is "FIRST.LAST". A Time.Net employee export, when given,
wins for names and supplies the department (the device holds no department). Nothing is created here: HR
reviews/edits the sheet and imports it with Horilla's existing import, which also refuses duplicate badge IDs.
"""

import re
import struct
from collections import defaultdict
from dataclasses import dataclass, field
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Font

from employee.models import Employee

from ..connectors.base import ConnectorError
from ..connectors.manual_file import _read_rows
from ..models import RawTransaction
from . import employee_mapping

HORILLA_HEADERS = [
    "Badge ID", "First Name", "Last Name", "Email", "Phone", "Gender", "Department", "Job Position",
    "Job Role", "Shift", "Work Type", "Reporting Manager", "Employee Type", "Location", "Date Joining",
    "Basic Salary", "Salary Hour", "CTC", "Contract End Date", "Company",
]
_USER = struct.Struct("<HB8s24sIx7sx24s")  # the 72-byte user record of ZKTeco face/finger devices


class WorkerSheetError(Exception):
    pass


@dataclass
class DeviceUser:
    id: str
    name: str
    privilege: int
    uid: int


@dataclass
class SheetRow:
    id: str
    first: str = ""
    last: str = ""
    department: str = ""
    device_name: str = ""
    source: str = "device"
    notes: list = field(default_factory=list)


def _text(b):
    return b.split(b"\x00")[0].decode("utf-8", "replace").strip()


def parse_user_dat(data: bytes):
    if not data:
        raise WorkerSheetError("The user file is empty.")
    if len(data) % _USER.size:
        raise WorkerSheetError(
            f"Unrecognised user file: its size ({len(data)} bytes) is not a multiple of {_USER.size}. "
            "This reader supports the 72-byte user records of the uFace/ZKTeco devices. Export the user list from Time.Net instead."
        )
    users = []
    for i in range(0, len(data), _USER.size):
        uid, priv, _pw, name, _card, _grp, user_id = _USER.unpack(data[i : i + _USER.size])
        ident = _text(user_id)
        if ident:
            users.append(DeviceUser(ident, _text(name), priv, uid))
    return users


def split_name(name):
    """'ADE.BAYO' -> ('ADE', 'BAYO', note). The device joins first and last name with a full stop."""
    name = (name or "").strip()
    if "." in name:
        parts = [p.strip() for p in name.split(".") if p.strip()]
        if len(parts) == 2:
            return parts[0], parts[1], ""
        if len(parts) > 2:
            return parts[0], " ".join(parts[1:]), "more than one full stop in the device name: check first/last name"
        return name.strip("."), "", "no surname on the device"
    parts = name.split()
    if len(parts) >= 2:
        return parts[0], " ".join(parts[1:]), "no full stop in the device name: split on the space, check it"
    return name, "", "no surname on the device"


ALIASES = {
    "id": ["emp id", "employee id", "emp no", "employee no", "emp code", "employee code", "badge id", "user id", "userid", "pin", "id", "no"],
    "first": ["first name", "firstname", "first"],
    "last": ["last name", "lastname", "surname", "last"],
    "full": ["name", "full name", "employee name", "emp name"],
    "dept": ["department", "dept", "dept name", "department name"],
}


def read_timenet_export(file_obj, filename):
    """Employee list exported from Time.Net (CSV/XLSX; headers found by name). Returns {ID: dict}."""
    rows = [r for r in _read_rows(file_obj, filename) if any(str(c).strip() for c in r if c is not None)]
    if not rows:
        raise WorkerSheetError("The Time.Net file is empty.")
    header = [" ".join(str(h or "").strip().lower().replace("_", " ").split()) for h in rows[0]]
    col = {}
    for key, names in ALIASES.items():
        for n in names:
            if n in header:
                col[key] = header.index(n)
                break
    if "id" not in col:
        raise WorkerSheetError("The Time.Net file needs an employee ID column (e.g. 'Emp ID' or 'Employee No').")
    if not ({"first", "full"} & set(col)):
        raise WorkerSheetError("The Time.Net file needs name columns (First Name / Last Name, or Name).")

    def cell(r, k):
        i = col.get(k)
        return "" if i is None or i >= len(r) or r[i] is None else str(r[i]).strip()

    out = {}
    for r in rows[1:]:
        ident = cell(r, "id")
        if re.fullmatch(r"\d+\.0+", ident):
            ident = ident.split(".")[0]
        if not ident:
            continue
        first, last = cell(r, "first"), cell(r, "last")
        if not first and cell(r, "full"):
            parts = cell(r, "full").split()
            first, last = parts[0], " ".join(parts[1:])
        out[ident.upper()] = {"first": first, "last": last, "dept": cell(r, "dept")}
    return out


def placeholder_email(ident, domain):
    return f"{re.sub(r'[^a-z0-9]+', '', ident.lower()) or 'worker'}@{domain}"


def placeholder_phone(ident):
    digits = re.sub(r"\D", "", ident) or "0"
    return f"0800{int(digits) % 10**7:07d}"  # valid-looking placeholder; HR replaces with real numbers


def build_rows(device_users, timenet=None, exclude_ids=(), provider=None):
    exclude = {e.strip().upper() for e in exclude_ids if e and e.strip()}
    existing_badges = {(b or "").strip().upper(): b for b in Employee.objects.entire().values_list("badge_id", flat=True) if b}
    existing_names = defaultdict(list)
    for badge, f, l in Employee.objects.values_list("badge_id", "employee_first_name", "employee_last_name"):
        existing_names[f"{f} {l or ''}".strip().lower()].append(badge or "")
    punched = set()
    if provider is not None:
        punched = set(RawTransaction.objects.filter(provider=provider).values_list("external_employee_id", flat=True))

    rows, excluded = [], []
    seen_names = defaultdict(list)
    listed = {u.id.upper() for u in device_users}
    for u in device_users:
        key = u.id.upper()
        if key in exclude:
            excluded.append((u.id, u.name, "left out by you"))
            continue
        if key in existing_badges:
            excluded.append((u.id, u.name, "already a worker in Horilla (badge ID exists)"))
            continue
        first, last, note = split_name(u.name)
        row = SheetRow(u.id, first, last, device_name=u.name)
        if note:
            row.notes.append(note)
        if timenet and key in timenet:
            t = timenet[key]
            row.source = "Time.Net"
            if t["first"]:
                row.first, row.last = t["first"], t["last"]
            row.department = t["dept"]
            if not t["dept"]:
                row.notes.append("no department in the Time.Net file")
        elif timenet is not None:
            row.notes.append("not found in the Time.Net file: names come from the device; add the department")
        if u.privilege not in (0, 255):  # 255 is stored for ordinary users on some devices
            row.notes.append(f"device privilege {u.privilege} (a device administrator/manager level; leave this ID out if not a worker)")
        if provider is not None and punched and u.id not in punched:
            row.notes.append("no punches recorded for this ID yet")
        rows.append(row)
        seen_names[f"{row.first} {row.last}".strip().lower()].append(row)
    for name, same in seen_names.items():
        if len(same) > 1:
            for r in same:
                r.notes.append("same name as " + ", ".join(o.id for o in same if o is not r) + ": one person with two IDs?")
        if name in existing_names:
            for r in same:
                r.notes.append(f"same name as an existing Horilla employee (badge {', '.join(existing_names[name]) or '-'})")
    unknown_punchers = []
    if provider is not None:
        for row in employee_mapping.unmapped_summary(provider):
            if row["external_employee_id"].upper() not in listed:
                unknown_punchers.append((row["external_employee_id"], row["count"]))
    timenet_only = []
    if timenet:
        timenet_only = sorted(k for k in timenet if k not in listed)
    return rows, excluded, unknown_punchers, timenet_only


def build_workbook(rows, excluded, unknown_punchers, timenet_only, *, company, defaults, email_domain):
    wb = Workbook()
    ws = wb.active
    ws.title = "WORKERS"  # the sheet Horilla's import reads: exactly its 20 headers, nothing extra
    ws.append(HORILLA_HEADERS)
    for r in rows:
        ws.append([
            r.id, r.first, r.last, placeholder_email(r.id, email_domain), placeholder_phone(r.id), "",
            r.department, defaults.get("job_position", ""), defaults.get("job_role", ""), defaults.get("shift", ""),
            defaults.get("work_type", ""), "", defaults.get("employee_type", ""), defaults.get("location", ""),
            defaults.get("date_joining") or None, None, None, None, None, company,
        ])
    for c in ws[1]:
        c.font = Font(bold=True)
    for row in ws.iter_rows(min_row=2, min_col=5, max_col=5):
        row[0].number_format = "@"
    for i, h in enumerate(HORILLA_HEADERS, 1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = max(12, len(h) + 2)

    rv = wb.create_sheet("REVIEW")
    rv.append(["ID", "Name on device", "First name", "Last name", "Department", "Name source", "Check"])
    for r in rows:
        rv.append([r.id, r.device_name, r.first, r.last, r.department, r.source, "; ".join(r.notes)])
    ex = wb.create_sheet("NOT INCLUDED")
    ex.append(["ID", "Name on device", "Why"])
    for e in excluded:
        ex.append(list(e))
    if unknown_punchers:
        ex.append([])
        ex.append(["Punched on the device but NOT on its user list", "Punches", ""])
        for ident, n in unknown_punchers:
            ex.append([ident, n, "unknown ID: left over from a deleted user or a test, or a worker missing from the device list"])
    if timenet_only:
        ex.append([])
        ex.append(["In the Time.Net file but not on the device user list", "", ""])
        for ident in timenet_only:
            ex.append([ident, "", "not on this device: leaver, or enrolled on another device"])
    for sheet in (rv, ex):
        for c in sheet[1]:
            c.font = Font(bold=True)
        sheet.column_dimensions["A"].width = 24
        sheet.column_dimensions["B"].width = 26
        sheet.column_dimensions["G" if sheet is rv else "C"].width = 80
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
