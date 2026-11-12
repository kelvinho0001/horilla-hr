import io
import re
import struct
from datetime import datetime
from zoneinfo import ZoneInfo

import openpyxl
import pandas as pd
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from base.models import Company
from biometric_integration.connectors.base import ConnectorError
from biometric_integration.connectors.manual_file import ManualFileConnector, device_serial_from_filename
from biometric_integration.models import IgnoredExternalID, ProviderConfig, RawTransaction
from biometric_integration.services import attendance_sync, employee_mapping, worker_sheet
from biometric_integration.tests.test_import import make_employee
from employee.methods.methods import process_employee_records, valid_import_file_headers

LAGOS = ZoneInfo("Africa/Lagos")
# Same shape as the device file: space-padded IDs, TAB separators, CRLF, four trailing codes, a blank line.
ATTLOG = (
    "      333\t2026-11-09 06:02:07\t1\t1\t1\t0\r\n"
    "    SE015\t2026-11-09 16:26:11\t2\t0\t1\t0\r\n"
    "    SE015\t2026-11-09 16:27:02\t2\t0\t1\t0\r\n"
    "\r\n"
    "    SE022\t2026-11-10 04:52:44\t2\t0\t1\t0\r\n"
)


def user_record(uid, user_id, name, priv=0):
    return struct.pack("<HB8s24sIx7sx24s", uid, priv, b"", name.encode(), 0, b"", user_id.encode())


def user_dat(*users):
    return b"".join(user_record(i, uid, name, priv) for i, (uid, name, priv) in enumerate(users, start=1))


class AttlogParsingTests(TestCase):
    def setUp(self):
        self.provider = ProviderConfig.objects.create(name="D", provider_type="manual_file")
        self.conn = ManualFileConnector(self.provider)

    def parse(self, text=ATTLOG, name="AEYU200860006_attlog.dat"):
        return self.conn.parse(io.BytesIO(text.encode()), name)

    def test_parses_padded_ids_blank_lines_and_keeps_codes(self):
        txns, errors = self.parse()
        self.assertEqual(errors, [])
        self.assertEqual([t.external_employee_id for t in txns], ["333", "SE015", "SE015", "SE022"])
        self.assertEqual(txns[1].timestamp, datetime(2026, 11, 9, 16, 26, 11))
        self.assertEqual(txns[1].raw["field3"], "2")
        self.assertEqual(txns[1].raw["device_serial"], "AEYU200860006")

    def test_bad_lines_reported_good_lines_kept(self):
        txns, errors = self.parse("SE015\t2026-11-09 16:26:11\t1\r\ngarbage\r\nSE016\tnot-a-time\t1\r\n")
        self.assertEqual(len(txns), 1)
        self.assertEqual([e.row for e in errors], [2, 3])

    def test_empty_file_rejected(self):
        with self.assertRaises(ConnectorError):
            self.parse("\r\n\r\n")

    def test_serial_from_filename(self):
        self.assertEqual(device_serial_from_filename("AEYU200860006_attlog.dat"), "AEYU200860006")
        self.assertEqual(device_serial_from_filename("abc123-AEYU200860012_attlog.dat"), "AEYU200860012")
        self.assertEqual(device_serial_from_filename("1.dat"), "")


class UploadDatTests(TestCase):
    def setUp(self):
        self.user = make_employee("HR1").employee_user_id
        self.user.is_new_employee = False
        self.user.save()
        self.user.user_permissions.set(Permission.objects.filter(
            codename__in=["view_importbatch", "add_importbatch", "view_externalemployeemapping", "add_externalemployeemapping", "change_externalemployeemapping"]))
        self.user = get_user_model().objects.get(pk=self.user.pk)
        self.client.force_login(self.user)

    def upload(self, name="AEYU200860006_attlog.dat", text=ATTLOG, **extra):
        return self.client.post(reverse("bio-int-upload"), {"file": SimpleUploadedFile(name, text.encode()), **extra})

    def test_serial_named_file_creates_that_devices_provider_and_is_idempotent(self):
        self.assertEqual(self.upload().status_code, 302)
        p = ProviderConfig.objects.get(name="Device AEYU200860006")
        self.assertEqual(RawTransaction.objects.filter(provider=p).count(), 4)
        self.upload()
        self.assertEqual(RawTransaction.objects.count(), 4)

    def test_renamed_copy_of_same_file_still_deduplicates_when_device_chosen(self):
        self.upload()
        p = ProviderConfig.objects.get(name="Device AEYU200860006")
        self.upload(name="1.dat", provider=p.pk)
        self.assertEqual(RawTransaction.objects.count(), 4)

    def test_two_devices_keep_ids_apart(self):
        self.upload()
        self.upload(name="AEYU200860012_attlog.dat")
        self.assertEqual(ProviderConfig.objects.filter(name__startswith="Device ").count(), 2)
        self.assertEqual(RawTransaction.objects.count(), 8)  # same IDs and times on two devices are NOT duplicates

    def test_dat_without_serial_goes_to_manual_upload(self):
        self.upload(name="1.dat")
        self.assertTrue(RawTransaction.objects.filter(provider__name="Manual upload").exists())


class UserDatTests(TestCase):
    def test_decodes_72_byte_records_and_splits_names(self):
        users = worker_sheet.parse_user_dat(user_dat(("SE015", "ADE.BAYO", 0), ("1", "admin", 14), ("SE374", "A.B.C", 255)))
        self.assertEqual([(u.id, u.name, u.privilege) for u in users],
                         [("SE015", "ADE.BAYO", 0), ("1", "admin", 14), ("SE374", "A.B.C", 255)])
        self.assertEqual(worker_sheet.split_name("ADE.BAYO"), ("ADE", "BAYO", ""))
        self.assertEqual(worker_sheet.split_name("ADE.BAYO.JOHN")[:2], ("ADE", "BAYO JOHN"))
        self.assertEqual(worker_sheet.split_name("aarinola kelvin")[:2], ("aarinola", "kelvin"))
        self.assertEqual(worker_sheet.split_name("admin")[1], "")

    def test_wrong_size_and_empty_rejected_clearly(self):
        for bad in (b"", b"x" * 100):
            with self.assertRaises(worker_sheet.WorkerSheetError):
                worker_sheet.parse_user_dat(bad)


class WorkerSheetTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(company="Demiller Farms", hq=True, address="x", country="NG", state="LA", city="Lagos", zip="1")
        self.provider = ProviderConfig.objects.create(name="Device AEYU200860006", provider_type="manual_file")
        self.users = worker_sheet.parse_user_dat(user_dat(
            ("SE015", "ADE.BAYO", 0), ("SE022", "CHI.OKO", 0), ("SE023", "CHI.OKO", 0),
            ("1", "admin", 14), ("104", "aarinola kelvin", 0), ("SE500", "NEW.PERSON", 0),
        ))

    def sheet(self, **kw):
        rows, excluded, unknown, tn_only = worker_sheet.build_rows(self.users, kw.get("timenet"), kw.get("exclude", ()), self.provider)
        data = worker_sheet.build_workbook(rows, excluded, unknown, tn_only, company="Demiller Farms", email_domain="workers.invalid",
                                           defaults={"employee_type": "Casual"})
        return rows, excluded, openpyxl.load_workbook(io.BytesIO(data))

    def test_exclusions_existing_workers_and_review_notes(self):
        make_employee("SE500", "Already", "There")  # badge SE500 already in Horilla
        rows, excluded, wb = self.sheet(exclude=["1", " 104 "])
        self.assertEqual([r.id for r in rows], ["SE015", "SE022", "SE023"])
        reasons = {e[0]: e[2] for e in excluded}
        self.assertEqual(reasons["1"], "left out by you")
        self.assertIn("already a worker", reasons["SE500"])
        notes = {r.id: "; ".join(r.notes) for r in rows}
        self.assertIn("same name as SE023", notes["SE022"])          # possible duplicate person
        self.assertEqual(notes["SE015"], "")
        self.assertEqual(wb.sheetnames, ["WORKERS", "REVIEW", "NOT INCLUDED"])

    def test_device_admin_level_is_flagged_not_dropped(self):
        rows, _e, _wb = self.sheet()
        self.assertIn("device privilege 14", "; ".join(next(r for r in rows if r.id == "1").notes))

    def test_timenet_export_wins_for_names_and_gives_department(self):
        csv = b"Emp ID,First Name,Last Name,Department\nSE015,Adebayo,Johnson,Packaging\nSE999,Not,OnDevice,Selecting\n"
        tn = worker_sheet.read_timenet_export(io.BytesIO(csv), "t.csv")
        rows, _e, wb = self.sheet(timenet=tn, exclude=["1", "104"])
        r = next(x for x in rows if x.id == "SE015")
        self.assertEqual((r.first, r.last, r.department, r.source), ("Adebayo", "Johnson", "Packaging", "Time.Net"))
        other = next(x for x in rows if x.id == "SE022")
        self.assertIn("not found in the Time.Net file", "; ".join(other.notes))
        self.assertIn("SE999", [c.value for row in wb["NOT INCLUDED"].iter_rows() for c in row])

    def test_unknown_punchers_listed(self):
        attendance_sync.import_transactions(self.provider, ManualFileConnector(self.provider).parse(
            io.BytesIO(b"55\t2026-11-03 08:35:31\t2\t0\t1\t0\r\n"), "x_attlog.dat")[0])
        _r, _e, wb = self.sheet()
        cells = [c.value for row in wb["NOT INCLUDED"].iter_rows() for c in row]
        self.assertIn("55", cells)

    def test_workers_sheet_is_accepted_by_horillas_own_import(self):
        rows, _e, wb = self.sheet(exclude=["1", "104"])
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        df = pd.read_excel(buf)  # the first sheet is what Horilla reads
        ok, msg = valid_import_file_headers(df)
        self.assertTrue(ok, msg)
        success, errors, created = process_employee_records(df.astype(object).where(df.notna(), None), self.company)
        self.assertEqual((created, errors), (4, []))  # blank department/shift/etc. are accepted
        self.assertEqual({e["Badge ID"] for e in success}, {"SE015", "SE022", "SE023", "SE500"})

    def test_second_import_of_same_sheet_is_refused_for_duplicates(self):
        make_employee("SE015", "Existing", "Worker")
        rows, excluded, _wb = self.sheet(exclude=["1", "104"])
        self.assertIn("SE015", [e[0] for e in excluded])  # never offered again


class WorkerSheetViewTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(company="Demiller Farms", hq=True, address="x", country="NG", state="LA", city="Lagos", zip="1")
        u = make_employee("HR2").employee_user_id
        u.is_new_employee = False
        u.save()
        u.user_permissions.set(Permission.objects.filter(codename__in=["add_externalemployeemapping", "view_importbatch"]))
        self.client.force_login(get_user_model().objects.get(pk=u.pk))

    def post(self, data, **kw):
        return self.client.post(reverse("bio-int-worker-sheet"), {
            "user_file": SimpleUploadedFile("user.dat", data), "company": self.company.pk,
            "email_domain": "workers.invalid", "employee_type": "Casual", **kw})

    def test_download_and_errors(self):
        r = self.post(user_dat(("SE015", "ADE.BAYO", 0), ("1", "admin", 14)), exclude_ids="1")
        self.assertEqual(r.status_code, 200)
        wb = openpyxl.load_workbook(io.BytesIO(r.content))
        self.assertEqual([c.value for c in wb["WORKERS"]["A"]][1:], ["SE015"])
        self.assertEqual(wb["WORKERS"]["B2"].value, "ADE")
        self.assertEqual(wb["WORKERS"]["E2"].data_type, "s")
        bad = self.post(b"not a user file")
        self.assertContains(bad, "Unrecognised user file")
        self.assertEqual(self.client.get(reverse("bio-int-worker-sheet")).status_code, 200)


class IgnoreAndBulkMapTests(TestCase):
    def setUp(self):
        self.p = ProviderConfig.objects.create(name="Device X", provider_type="manual_file")
        txns, _ = ManualFileConnector(self.p).parse(io.BytesIO(
            b"SE015\t2026-11-09 16:00:00\t2\t0\t1\t0\r\nSE022\t2026-11-09 16:01:00\t2\t0\t1\t0\r\n"
            b"55\t2026-11-03 08:35:31\t2\t0\t1\t0\r\n1\t2026-11-03 08:09:10\t2\t0\t1\t0\r\n"), "x_attlog.dat")
        self.batch = attendance_sync.import_transactions(self.p, txns)

    def test_map_all_only_maps_exact_badge_matches(self):
        a = make_employee("SE015", "A", "One")
        make_employee("SE022X", "B", "Two")  # near miss: not mapped
        mapped, skipped = employee_mapping.map_all_suggested(self.p)
        self.assertEqual((mapped, skipped), (1, 0))
        self.assertEqual(RawTransaction.objects.get(external_employee_id="SE015").employee, a)
        self.assertIsNone(RawTransaction.objects.get(external_employee_id="SE022").employee)

    def test_ignored_ids_disappear_from_unmapped_but_keep_their_punches(self):
        employee_mapping.ignore_id(self.p, "55", "test punch")
        employee_mapping.ignore_id(self.p, "1", "admin")
        ids = {r["external_employee_id"] for r in employee_mapping.unmapped_summary(self.p)}
        self.assertEqual(ids, {"SE015", "SE022"})
        self.assertEqual(RawTransaction.objects.filter(external_employee_id__in=["1", "55"]).count(), 2)
        with self.assertRaises(ValueError):
            employee_mapping.ignore_id(self.p, "9", "  ")
        employee_mapping.unignore_id(self.p, "55")
        self.assertIn("55", {r["external_employee_id"] for r in employee_mapping.unmapped_summary(self.p)})

    def test_next_upload_does_not_count_ignored_as_unmapped(self):
        employee_mapping.ignore_id(self.p, "55", "test")
        employee_mapping.ignore_id(self.p, "1", "admin")
        txns, _ = ManualFileConnector(self.p).parse(io.BytesIO(b"55\t2026-11-04 08:00:00\t2\t0\t1\t0\r\nSE099\t2026-11-04 16:00:00\t2\t0\t1\t0\r\n"), "x_attlog.dat")
        batch = attendance_sync.import_transactions(self.p, txns)
        self.assertEqual(batch.unmapped_count, 1)  # only SE099
