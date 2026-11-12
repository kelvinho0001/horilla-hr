from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from attendance.models import Attendance
from attendance_processing import calculators, permissions, services
from attendance_processing.models import AttendanceRule
from base.models import CompanyGroupAssignment, Department
from biometric_integration.models import ExternalEmployeeMapping, ImportBatch, ProviderConfig, RawTransaction
from horilla.testkit import make_company, make_employee

LAGOS = ZoneInfo("Africa/Lagos")
MON = date(2024, 9, 2)  # a Monday in the past
_N = [0]


def _bump():
    _N[0] += 1


def at(day, hh=16, mm=0):
    return datetime.combine(day, time(hh, mm), tzinfo=LAGOS)


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = make_company()
        cls.provider = ProviderConfig.objects.create(name="M", provider_type="manual_file")
        cls.batch = ImportBatch.objects.create(provider=cls.provider)

    def emp(self, joined=date(2024, 1, 1), department=None, mapped=True):
        _bump()
        e = make_employee(company=self.company, email=f"e{_N[0]}@x.test", first_name=f"E{_N[0]}",
                          date_joining=joined, department=department)
        if mapped:
            ExternalEmployeeMapping.objects.create(provider=self.provider, employee=e, external_employee_id=f"{_N[0]}")
        return e

    def punch(self, e, ts):
        _bump()
        return RawTransaction.objects.create(
            provider=self.provider, batch=self.batch, dedup_key=f"k{_N[0]}", external_employee_id="x", employee=e,
            attendance_timestamp=ts, source_timezone="Africa/Lagos")

    def att(self, e, d):
        return Attendance.objects.filter(employee_id=e, attendance_date=d).first()

    def window_rule(self, **kw):
        r = AttendanceRule.objects.create(name=f"r{_N[0]}", scope=kw.pop("scope", "global"), **kw)
        return r


class CalculatorTests(TestCase):
    def test_week_buckets_any_length(self):
        b = calculators.week_buckets(date(2024, 9, 1), date(2024, 9, 17))
        self.assertEqual([(a.day, z.day) for a, z in b], [(1, 7), (8, 14), (15, 17)])

    def test_any_number_of_valid_punches_is_one_ok_day(self):
        s, f = calculators.classify([time(15, 55), time(16, 5), time(16, 6)])
        self.assertEqual(s, "ok")
        self.assertIn("multiple_punches", f)

    def test_outside_every_window_needs_attention(self):
        self.assertEqual(calculators.classify([time(11)], [(time(0), time(9)), (time(14), time(20))]), ("attention", ["out_of_window"]))

    def test_two_shifts_in_one_day_needs_attention(self):
        w = [(time(0), time(9)), (time(14), time(20))]
        s, f = calculators.classify([time(7, 42), time(14, 35)], w)
        self.assertEqual(s, "attention")
        self.assertIn("two_shifts_same_day", f)

    def test_window_forms_accepted(self):
        self.assertEqual(calculators.classify([time(16)], (time(14), time(20)))[0], "ok")
        self.assertEqual(calculators.classify([time(16)], None)[0], "ok")
        self.assertEqual(calculators.classify([])[0], None)


class ProcessingTests(Base):
    def run_day(self, e, d=MON, e2=None):
        return services.process_range(d, d, [e])

    def test_valid_punch_becomes_an_unvalidated_horilla_attendance(self):
        e = self.emp()
        self.punch(e, at(MON, 16, 26))
        stats = self.run_day(e)
        a = self.att(e, MON)
        self.assertEqual(stats["created"], 1)
        self.assertEqual((a.attendance_clock_in, a.attendance_clock_in_date, a.attendance_validated), (time(16, 26), MON, False))
        self.assertIsNone(a.attendance_clock_out)

    def test_two_punches_one_day_is_one_record_with_checkout_when_hours_apart(self):
        e = self.emp()
        self.punch(e, at(MON, 8, 5))
        self.punch(e, at(MON, 18, 10))
        self.run_day(e)
        self.assertEqual(Attendance.objects.filter(employee_id=e).count(), 1)
        self.assertEqual(self.att(e, MON).attendance_clock_out, time(18, 10))

    def test_double_punch_minutes_apart_has_no_checkout(self):
        e = self.emp()
        self.punch(e, at(MON, 16, 26))
        self.punch(e, at(MON, 16, 27))
        self.run_day(e)
        self.assertIsNone(self.att(e, MON).attendance_clock_out)

    def test_rerun_is_idempotent_and_never_touches_existing_records(self):
        e = self.emp()
        self.punch(e, at(MON))
        self.run_day(e)
        Attendance.objects.filter(employee_id=e).update(attendance_validated=True)
        stats = self.run_day(e)
        self.assertEqual((stats["created"], stats["already_there"]), (0, 1))
        self.assertTrue(self.att(e, MON).attendance_validated)
        self.assertEqual(Attendance.objects.filter(employee_id=e).count(), 1)

    def test_day_with_hr_added_attendance_is_left_alone(self):
        e = self.emp()
        self.punch(e, at(MON))
        Attendance.objects.create(employee_id=e, attendance_date=MON, attendance_clock_in=time(9), attendance_clock_in_date=MON, attendance_validated=True)
        stats = self.run_day(e)
        self.assertEqual(stats["created"], 0)
        self.assertEqual(self.att(e, MON).attendance_clock_in, time(9))

    def test_day_is_the_lagos_date_not_utc(self):
        e = self.emp()
        self.punch(e, datetime(2024, 9, 2, 23, 30, tzinfo=ZoneInfo("UTC")))  # 00:30 Tuesday in Lagos
        services.process_range(MON, MON + timedelta(1), [e])
        self.assertIsNone(self.att(e, MON))
        self.assertIsNotNone(self.att(e, MON + timedelta(1)))

    def test_future_days_never_processed(self):
        e = self.emp()
        self.punch(e, at(MON))
        stats = services.process_range(services.today() + timedelta(1), services.today() + timedelta(5), [e])
        self.assertEqual(stats["created"], 0)

    def test_before_joining_and_after_contract_end_ignored(self):
        e = self.emp(joined=MON + timedelta(3))
        self.punch(e, at(MON))
        self.punch(e, at(MON + timedelta(3)))
        services.process_range(MON, MON + timedelta(6), [e])
        self.assertIsNone(self.att(e, MON))
        self.assertIsNotNone(self.att(e, MON + timedelta(3)))

    def test_only_mapped_workers_are_processed(self):
        e = self.emp(mapped=False)
        self.assertNotIn(e, list(services.employees_for_attendance()))

    def test_two_shift_windows_and_attention_cases(self):
        r = self.window_rule()
        r.windows.create(start=time(0), end=time(9))
        r.windows.create(start=time(14), end=time(20))
        night, day_w, between, both = self.emp(), self.emp(), self.emp(), self.emp()
        self.punch(night, at(MON, 4, 52))
        self.punch(day_w, at(MON, 16, 26))
        self.punch(between, at(MON, 11))
        self.punch(both, at(MON, 7, 42))
        self.punch(both, at(MON, 14, 35))
        stats = services.process_range(MON, MON, [night, day_w, between, both])
        self.assertEqual(stats["created"], 2)
        self.assertIsNotNone(self.att(night, MON))
        self.assertIsNotNone(self.att(day_w, MON))
        self.assertIsNone(self.att(between, MON))
        self.assertIsNone(self.att(both, MON))
        self.assertEqual({e.pk for e, _d, _i in stats["attention"]}, {between.pk, both.pk})

    def test_accept_day_adds_a_flagged_day_once(self):
        r = self.window_rule()
        r.windows.create(start=time(14), end=time(20))
        e = self.emp()
        self.punch(e, at(MON, 11))
        services.process_range(MON, MON, [e])
        self.assertIsNone(self.att(e, MON))
        services.accept_day(e, MON)
        self.assertFalse(self.att(e, MON).attendance_validated)
        with self.assertRaises(services.AttendanceError):
            services.accept_day(e, MON)
        with self.assertRaises(services.AttendanceError):
            services.accept_day(e, MON + timedelta(1))

    def test_rule_specificity_department_beats_global(self):
        d = Department.objects.create(department="Packing")
        g = self.window_rule()
        g.windows.create(start=time(15), end=time(17))
        k = self.window_rule(scope="department", department=d)
        k.windows.create(start=time(9), end=time(11))
        a, b = self.emp(department=d), self.emp()
        for e in (a, b):
            self.punch(e, at(MON, 10))
        services.process_range(MON, MON, [a, b])
        self.assertIsNotNone(self.att(a, MON))   # department window 9-11
        self.assertIsNone(self.att(b, MON))      # global window 15-17

    def test_range_limit(self):
        with self.assertRaises(services.AttendanceError):
            services.process_range(MON, MON + timedelta(200), [])


class RoleAndViewTests(Base):
    def setUp(self):
        permissions.sync_groups()
        self.e = self.emp()
        self.punch(self.e, at(MON, 16))

    def login(self, group):
        user = make_employee(company=self.company, email=f"{group.replace('/', '')}{_N[0]}@x.test").employee_user_id
        _bump()
        user.is_new_employee = False
        user.save()
        CompanyGroupAssignment.objects.create(user=user, company=self.company, group=Group.objects.get(name=group))
        self.client.force_login(user)
        return user

    def test_officer_can_process_accountant_cannot(self):
        self.login("Accountant")
        self.client.post(reverse("att-proc-process"), {"start": str(MON), "end": str(MON)})
        self.assertIsNone(self.att(self.e, MON))
        self.login("HR Officer")
        self.client.post(reverse("att-proc-process"), {"start": str(MON), "end": str(MON)})
        self.assertIsNotNone(self.att(self.e, MON))

    def test_hr_roles_carry_horillas_attendance_permissions_for_validating(self):
        for role in ("HR Officer", "HR/Payroll Approver"):
            codes = set(Group.objects.get(name=role).permissions.filter(content_type__app_label="attendance").values_list("codename", flat=True))
            self.assertTrue({"view_attendance", "add_attendance", "change_attendance"} <= codes, role)

    def test_overview_ranges_and_states(self):
        self.login("HR Officer")
        self.client.post(reverse("att-proc-process"), {"start": str(MON), "end": str(MON + timedelta(6))})
        for qs in ({"start": str(MON), "end": str(MON + timedelta(2))}, {"from_month": "2024-08", "to_month": "2024-09"}, {}):
            self.assertEqual(self.client.get(reverse("att-proc-review"), qs).status_code, 200, qs)
        r = self.client.get(reverse("att-proc-review"), {"start": "2024-01-01", "end": "2024-12-31"})
        self.assertContains(r, "at most 93 days")
        r = self.client.get(reverse("att-proc-review"), {"start": str(MON), "end": str(MON + timedelta(9))})
        self.assertContains(r, "Wk2")
        self.assertContains(r, "waiting for validation")
        Attendance.objects.filter(employee_id=self.e).update(attendance_validated=True)
        self.assertContains(self.client.get(reverse("att-proc-review"), {"start": str(MON), "end": str(MON)}), "1 validated")

    def test_day_page_and_accept_flow(self):
        r = self.window_rule()
        r.windows.create(start=time(0), end=time(9))
        self.login("HR Officer")
        page = self.client.get(reverse("att-proc-day", args=[self.e.pk, str(MON)]))
        self.assertContains(page, "Needs attention")
        self.client.post(reverse("att-proc-accept"), {"employee": self.e.pk, "date": str(MON)})
        self.assertIsNotNone(self.att(self.e, MON))

    def test_rule_page_saves_windows_and_rejects_overlap(self):
        self.login("HR/Payroll Approver")
        base = {"name": "contract", "scope": "global", "is_active": "on", "windows-TOTAL_FORMS": "2",
                "windows-INITIAL_FORMS": "0", "windows-MIN_NUM_FORMS": "0", "windows-MAX_NUM_FORMS": "1000"}
        bad = {**base, "windows-0-start": "00:00", "windows-0-end": "09:00", "windows-1-start": "08:00", "windows-1-end": "12:00"}
        self.assertEqual(self.client.post(reverse("att-proc-rule-new"), bad).status_code, 200)
        self.assertEqual(AttendanceRule.objects.count(), 0)
        good = {**base, "windows-0-start": "00:00", "windows-0-end": "09:00", "windows-1-start": "14:00", "windows-1-end": "20:00"}
        self.assertEqual(self.client.post(reverse("att-proc-rule-new"), good).status_code, 302)
        rule = AttendanceRule.objects.get()
        self.assertEqual([(str(a), str(b)) for a, b in rule.window_list()], [("00:00:00", "09:00:00"), ("14:00:00", "20:00:00")])
        self.assertContains(self.client.get(reverse("att-proc-rules")), "14:00–20:00")
        self.assertEqual(self.client.get(reverse("att-proc-rule-edit", args=[rule.pk])).status_code, 200)

    def test_officer_cannot_edit_rules(self):
        self.login("HR Officer")
        self.assertNotEqual(self.client.get(reverse("att-proc-rule-new")).status_code, 200)
