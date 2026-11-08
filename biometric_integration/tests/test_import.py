import io
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from biometric_integration.connectors.base import ConnectorError, NotSupported
from biometric_integration.connectors.direct_zkteco import DirectZKTecoConnector
from biometric_integration.connectors.manual_file import ManualFileConnector
from biometric_integration.connectors.registry import get_connector
from biometric_integration.models import (
    ExternalEmployeeMapping,
    ImportBatch,
    ProviderConfig,
    RawTransaction,
)
from biometric_integration.services import attendance_sync, employee_mapping
from employee.models import Employee

User = get_user_model()

CSV = (
    "Emp ID,Name,Date,Time\n"
    "SE001,Adigun,01/09/2025,16:02:10\n"
    "SE001,Adigun,01/09/2025,16:05:00\n"
    "SE002,David,01/09/2025,15:58\n"
    "SE001,Adigun,not-a-date,16:00\n"
    ",Nobody,02/09/2025,16:00\n"
)


def make_employee(badge, first="A", last="B"):
    n = Employee.objects.count() + 1
    user = User.objects.create_user(username=f"u{badge}{n}", password="x")
    return Employee.objects.create(
        employee_user_id=user,
        employee_first_name=first,
        employee_last_name=last,
        email=f"{badge}{n}@example.com",
        phone="0800000000",
        badge_id=badge,
    )


class ImportTests(TestCase):
    def setUp(self):
        self.provider = ProviderConfig.objects.create(
            name="Manual", provider_type="manual_file"
        )
        self.connector = ManualFileConnector(self.provider)

    def parse(self, text=CSV, name="r.csv"):
        return self.connector.parse(io.BytesIO(text.encode()), name)

    def run_import(self, text=CSV):
        txns, errs = self.parse(text)
        return attendance_sync.import_transactions(self.provider, txns, errs, file_name="r.csv")

    def test_parse_reports_bad_rows(self):
        txns, errs = self.parse()
        self.assertEqual(len(txns), 3)
        self.assertEqual(sorted(e.row for e in errs), [5, 6])

    def test_import_twice_creates_no_duplicates(self):
        first = self.run_import()
        second = self.run_import()
        self.assertEqual(RawTransaction.objects.count(), 3)
        self.assertEqual(first.imported_count, 3)
        self.assertEqual(second.imported_count, 0)
        self.assertEqual(second.duplicate_count, 3)

    def test_multiple_punches_same_day_all_retained(self):
        self.run_import()
        self.assertEqual(RawTransaction.objects.filter(external_employee_id="SE001").count(), 2)

    def test_unknown_ids_reported_as_unmapped(self):
        batch = self.run_import()
        self.assertEqual(batch.unmapped_count, 3)
        self.assertEqual(batch.status, ImportBatch.PARTIAL)
        ids = {r["external_employee_id"] for r in employee_mapping.unmapped_summary(self.provider)}
        self.assertEqual(ids, {"SE001", "SE002"})

    def test_mapping_applies_and_relinks_old_rows(self):
        self.run_import()
        emp = make_employee("SE001")
        employee_mapping.map_employee(self.provider, emp, "SE001")
        self.assertEqual(
            RawTransaction.objects.filter(employee=emp, processing_status="mapped").count(), 2
        )
        # later import of new punches for the same ID is mapped immediately
        extra = CSV.replace("01/09/2025", "02/09/2025")
        batch = self.run_import(extra)
        self.assertEqual(
            RawTransaction.objects.filter(employee=emp).count(), 4
        )
        self.assertEqual(batch.unmapped_count, 1)  # SE002 only

    def test_late_arriving_older_records_import(self):
        self.run_import(CSV.replace("01/09/2025", "10/09/2025"))
        batch = self.run_import(CSV)  # older week arrives afterwards
        self.assertEqual(batch.imported_count, 3)

    def test_naive_times_use_source_timezone(self):
        self.run_import()
        t = RawTransaction.objects.filter(external_employee_id="SE002").get()
        self.assertEqual(
            t.attendance_timestamp,
            datetime(2025, 9, 1, 15, 58, tzinfo=ZoneInfo("Africa/Lagos")),
        )

    def test_future_timestamp_rejected(self):
        batch = self.run_import("Emp ID,Datetime\nSE001,2999-01-01 10:00\n")
        self.assertEqual(batch.imported_count, 0)
        self.assertEqual(batch.failed_count, 1)

    def test_datetime_column_and_excel_style_ids(self):
        txns, errs = self.parse("User ID,Date/Time\n12.0,2025-09-01 16:00:00\n")
        self.assertEqual(txns[0].external_employee_id, "12")

    def test_missing_columns_raise(self):
        with self.assertRaises(ConnectorError):
            self.parse("Foo,Bar\n1,2\n")

    def test_original_data_preserved(self):
        self.run_import()
        t = RawTransaction.objects.filter(external_employee_id="SE002").get()
        self.assertEqual(t.original_data["Name"], "David")

    def test_same_batch_internal_duplicates_collapsed(self):
        batch = self.run_import("Emp ID,Datetime\nA,2025-09-01 16:00\nA,2025-09-01 16:00\n")
        self.assertEqual((batch.imported_count, batch.duplicate_count), (1, 1))


class MappingTests(TestCase):
    def setUp(self):
        self.p = ProviderConfig.objects.create(name="M", provider_type="manual_file")
        self.q = ProviderConfig.objects.create(name="N", provider_type="manual_file")

    def test_id_cannot_map_to_two_employees(self):
        a, b = make_employee("E1"), make_employee("E2")
        employee_mapping.map_employee(self.p, a, "7")
        with self.assertRaises(employee_mapping.MappingConflict):
            employee_mapping.map_employee(self.p, b, "7")

    def test_same_id_allowed_in_other_provider_and_after_deactivation(self):
        a, b = make_employee("E1"), make_employee("E2")
        m = employee_mapping.map_employee(self.p, a, "7")
        employee_mapping.map_employee(self.q, b, "7")
        employee_mapping.deactivate_mapping(m)
        employee_mapping.map_employee(self.p, b, "7")

    def test_one_employee_many_mappings(self):
        a = make_employee("E1")
        employee_mapping.map_employee(self.p, a, "7")
        employee_mapping.map_employee(self.p, a, "8")
        self.assertEqual(a.biometric_mappings.count(), 2)

    def test_suggestion_by_badge_only_and_not_ambiguous(self):
        a = make_employee("E1", "Same", "Name")
        make_employee("E2", "Same", "Name")
        self.assertEqual(employee_mapping.suggest_employee("E1"), a)
        self.assertIsNone(employee_mapping.suggest_employee("Same Name"))

    def test_mapping_change_audited(self):
        from auditlog.models import LogEntry

        a = make_employee("E1")
        m = employee_mapping.map_employee(self.p, a, "7")
        employee_mapping.deactivate_mapping(m)
        self.assertGreaterEqual(LogEntry.objects.get_for_object(m).count(), 2)


class ConnectorContractTests(TestCase):
    def test_capabilities_declared_and_enforced(self):
        p = ProviderConfig.objects.create(name="M", provider_type="manual_file")
        c = get_connector(p)
        self.assertFalse(c.supports("fetch_employees"))
        with self.assertRaises(NotSupported):
            c.fetch_employees()

    def test_unregistered_provider_clear_error(self):
        p = ProviderConfig.objects.create(name="B", provider_type="biotime")
        with self.assertRaises(NotSupported):
            get_connector(p)

    def test_direct_zk_normalises_and_never_clears_device(self):
        from biometric.models import BiometricDevices

        dev = BiometricDevices.objects.create(
            name="d", machine_type="zk", machine_ip="10.0.0.5", port=4370, zk_password="0"
        )
        p = ProviderConfig.objects.create(name="Z", provider_type="direct_zkteco", device=dev)
        log = mock.Mock(user_id="5", timestamp=datetime(2025, 9, 1, 16, 0), status=1, uid=9, punch=0)
        conn = mock.Mock()
        conn.get_attendance.return_value = [log]
        with mock.patch("zk.ZK") as zk:
            zk.return_value.connect.return_value = conn
            txns, errs = DirectZKTecoConnector(p).fetch_transactions()
        self.assertEqual(txns[0].external_employee_id, "5")
        conn.clear_attendance.assert_not_called()
        conn.disconnect.assert_called_once()

    def test_provider_switch_leaves_downstream_unchanged(self):
        """Two providers produce the same RawTransaction shape for the same punch."""
        for name in ("A", "B"):
            p = ProviderConfig.objects.create(name=name, provider_type="manual_file")
            txns, _ = ManualFileConnector(p).parse(
                io.BytesIO(b"Emp ID,Datetime\n1,2025-09-01 16:00\n"), "x.csv"
            )
            attendance_sync.import_transactions(p, txns)
        a, b = RawTransaction.objects.order_by("provider_id")
        self.assertEqual(a.attendance_timestamp, b.attendance_timestamp)
        self.assertEqual(a.external_employee_id, b.external_employee_id)


class ViewTests(TestCase):
    def setUp(self):
        # Horilla's login_required also demands an active Employee profile.
        self.user = make_employee("HR1").employee_user_id
        self.client.force_login(self.user)

    def grant(self, *codenames):
        self.user.user_permissions.set(Permission.objects.filter(codename__in=codenames))
        self.user = User.objects.get(pk=self.user.pk)
        self.client.force_login(self.user)

    def test_requires_permission(self):
        r = self.client.get(reverse("bio-int-dashboard"))
        self.assertNotEqual(r.status_code, 200)
        self.grant("view_importbatch")
        self.assertEqual(self.client.get(reverse("bio-int-dashboard")).status_code, 200)

    def test_mapping_pages(self):
        self.grant("view_externalemployeemapping", "add_externalemployeemapping")
        for name in ("bio-int-mapping-list", "bio-int-mapping-create", "bio-int-unmapped"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)

    def test_upload_flow(self):
        self.grant("view_importbatch", "add_importbatch")
        from django.core.files.uploadedfile import SimpleUploadedFile

        f = SimpleUploadedFile("r.csv", CSV.encode())
        r = self.client.post(reverse("bio-int-upload"), {"file": f})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(RawTransaction.objects.count(), 3)
        self.assertEqual(self.client.get(r.url).status_code, 200)
        self.assertEqual(self.client.get(reverse("bio-int-dashboard")).status_code, 200)
        self.assertEqual(self.client.get(reverse("bio-int-batches")).status_code, 200)
