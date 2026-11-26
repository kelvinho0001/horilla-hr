import io
from unittest import mock

import pandas as pd
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from bank_export import paystack
from bank_export.models import AccountVerification, Bank, PaystackSettings
from employee.models import Employee, EmployeeBankDetails

from horilla.testkit import make_company, make_employee, make_user


def admin():
    u = make_user("root", is_superuser=True)
    u.is_new_employee = False
    u.save()
    make_employee(company=make_company(), email="admin@x.test", user=u)
    return u

HEADERS = ["Badge ID", "First Name", "Last Name", "Phone", "Email", "Gender", "Department", "Job Position", "Job Role",
           "Work Type", "Shift", "Employee Type", "Reporting Manager", "Company", "Location", "Date Joining",
           "Contract End Date", "Basic Salary", "Salary Hour", "Bank Name", "Account Number", "Bank Code 1"]


def reply(code=200, **body):
    r = mock.Mock(status_code=code)
    r.json.return_value = body
    return r


def paystack_ok(url, params=None, headers=None, timeout=None):
    names = {"0123456789": "ADA OBI", "1111111111": "SOMEONE ELSE"}
    n = names.get(params["account_number"])
    if n:
        return reply(200, status=True, data={"account_name": n})
    return reply(422, status=False, message="Could not resolve account name. Check parameters or try again.")


def row(badge, first, last, acct, code, email=None):
    r = {h: None for h in HEADERS}
    r.update({"Badge ID": badge, "First Name": first, "Last Name": last, "Phone": "08012345678",
              "Email": email or f"{badge.lower()}@x.test", "Gender": "male", "Department": "Farm",
              "Job Position": "Worker", "Job Role": "Worker", "Work Type": "Onsite", "Shift": "Day",
              "Employee Type": "Contract", "Company": "Test Corp", "Date Joining": "2025-01-05", "Basic Salary": 30000,
              "Account Number": acct, "Bank Code 1": code})
    return r


class PaystackImportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Bank.objects.create(code="044", name="Access Bank")
        cls.su = admin()

    def import_rows(self, rows):
        buf = io.BytesIO()
        pd.DataFrame(rows, columns=HEADERS).to_excel(buf, index=False)
        self.client.force_login(self.su)
        return self.client.post(
            reverse("work-info-import"),
            {"file": SimpleUploadedFile("e.xlsx", buf.getvalue())}, HTTP_HX_REQUEST="true",
        )

    def key(self, key="sk_test_abc"):
        PaystackSettings.objects.create(secret_key=key)

    @mock.patch("bank_export.paystack.requests.get", side_effect=paystack_ok)
    def test_import_fills_bank_name_from_code_and_verifies(self, get):
        self.key()
        resp = self.import_rows([
            row("T1", "Ada", "Obi", "0123456789", "044"),
            row("T2", "Bola", "Eze", 1111111111, 44),          # Excel numbers: 44 -> 044
            row("T3", "Chi", "Ude", "5555555555", "044"),       # Paystack cannot find it
            row("T4", "Dayo", "Ola", "0123456780", "999"),      # code not in bank list
            row("T5", "Efe", "Uko", "12345", "044"),            # not 10 digits
        ])
        self.assertEqual(resp.status_code, 200)
        t1 = EmployeeBankDetails.objects.get(employee_id__badge_id="T1")
        self.assertEqual((t1.bank_name, t1.any_other_code1, t1.branch, t1.account_number),
                         ("Access Bank", "044", "Lagos", "0123456789"))
        v = t1.verification
        self.assertEqual((v.status, v.account_name, v.name_matches), ("verified", "ADA OBI", True))
        t2 = EmployeeBankDetails.objects.get(employee_id__badge_id="T2")
        self.assertEqual(t2.any_other_code1, "044")
        self.assertFalse(t2.verification.name_matches)         # SOMEONE ELSE vs Bola Eze
        self.assertEqual(EmployeeBankDetails.objects.get(employee_id__badge_id="T3").verification.status, "failed")
        for badge in ("T4", "T5"):                              # employee still created, bank not saved
            self.assertTrue(Employee.objects.filter(badge_id=badge).exists())
            self.assertFalse(EmployeeBankDetails.objects.filter(employee_id__badge_id=badge).exclude(account_number__in=[None, ""]).exists())
        text = resp.content.decode()
        self.assertIn("T4", text)
        self.assertIn("not in the bank list", text)
        self.assertIn("T3", text)
        self.assertEqual(get.call_args.kwargs["headers"]["Authorization"], "Bearer sk_test_abc")

    @mock.patch("bank_export.paystack.requests.get", side_effect=paystack_ok)
    def test_without_a_key_bank_is_saved_but_not_checked(self, get):
        resp = self.import_rows([row("T1", "Ada", "Obi", "0123456789", "044")])
        d = EmployeeBankDetails.objects.get(employee_id__badge_id="T1")
        self.assertEqual(d.bank_name, "Access Bank")
        self.assertFalse(AccountVerification.objects.exists())
        get.assert_not_called()
        self.assertEqual(resp.status_code, 200)

    @mock.patch("bank_export.paystack.requests.get", side_effect=paystack_ok)
    def test_changed_account_voids_the_old_check(self, get):
        self.key()
        self.import_rows([row("T1", "Ada", "Obi", "0123456789", "044")])
        d = EmployeeBankDetails.objects.get(employee_id__badge_id="T1")
        self.assertIsNotNone(paystack.current_status(d))
        d.account_number = "1111111111"
        d.save()
        d = EmployeeBankDetails.objects.get(pk=d.pk)
        self.assertIsNone(paystack.current_status(d))

    def test_bad_key_or_outage_is_not_blamed_on_the_account(self):
        with mock.patch("bank_export.paystack.requests.get", return_value=reply(401, status=False, message="Invalid key")):
            res = paystack.resolve("0123456789", "044", key="sk_test_x")
        self.assertEqual(res.status, "error")


class PaystackSettingsPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.su = admin()

    def test_key_is_saved_masked_and_kept_when_left_blank(self):
        self.client.force_login(self.su)
        url = reverse("bx-paystack")
        self.client.post(url, {"secret_key": "sk_live_abcdefghijklmnop", "is_enabled": "on"})
        self.assertEqual(PaystackSettings.objects.get().secret_key, "sk_live_abcdefghijklmnop")
        page = self.client.get(url).content.decode()
        self.assertNotIn("abcdefghijklmnop", page)
        self.client.post(url, {"secret_key": "", "is_enabled": "on"})
        self.assertEqual(PaystackSettings.objects.get().secret_key, "sk_live_abcdefghijklmnop")
        r = self.client.post(url, {"secret_key": "not-a-key", "is_enabled": "on"})
        self.assertContains(r, "not a Paystack secret key")

    def test_page_needs_the_permission(self):
        u = make_user("plain")
        u.is_new_employee = False
        u.save()
        make_employee(company=make_company("Other"), email="plain@x.test", user=u)
        self.client.force_login(u)
        self.assertNotEqual(self.client.get(reverse("bx-paystack")).status_code, 200)


class MasterImportBankTests(TestCase):
    """The Employees import (master_import): Bank Name is derived from Bank Code 1; account name comes from Paystack."""

    @classmethod
    def setUpTestData(cls):
        Bank.objects.create(code="044", name="Access Bank")
        cls.company = make_company("Test Corp")

    def rows(self, *specs):
        out = []
        for i, (acct, code) in enumerate(specs, start=1):
            r = row(f"M{i}", "Ada" if i == 1 else f"Name{i}", "Obi" if i == 1 else "Eze", acct, code)
            r["Company"] = "Test Corp"
            r["__row"] = i
            out.append({k: v for k, v in r.items() if v is not None})
        return out

    def test_bank_name_and_branch_come_from_the_code_and_bank_problems_are_row_errors(self):
        from master_import.datasets.base import Context
        from master_import.datasets.employees import EmployeesDataset

        ds = EmployeesDataset()
        ctx = Context(company=self.company)
        res = ds.check(self.rows(("0123456789", "44"), ("5555555555", "999"), ("12345", "044"), ("0123456789", "044")), ctx)
        ok = {r["Badge ID"]: r for r in res.valid}
        self.assertEqual(set(ok), {"M1"})
        self.assertEqual((ok["M1"]["Bank Name"], ok["M1"]["Bank Code 1"], ok["M1"]["Bank Branch"]),
                         ("Access Bank", "044", "Lagos"))
        text = " ".join(str(e) for e in res.errors)
        self.assertIn("not in the bank list", text)
        self.assertIn("10 digits", text)
        self.assertIn("used twice", text)

    @mock.patch("bank_export.paystack.requests.get", side_effect=paystack_ok)
    def test_commit_saves_name_and_verifies(self, get):
        from master_import.datasets.base import Context
        from master_import.datasets.employees import EmployeesDataset

        PaystackSettings.objects.create(secret_key="sk_test_abc")
        ds = EmployeesDataset()
        ctx = Context(company=self.company)
        res = ds.check(self.rows(("0123456789", "044")), ctx)
        out = ds.commit(res.valid, ctx)
        d = EmployeeBankDetails.objects.get(employee_id__badge_id="M1")
        self.assertEqual((d.bank_name, d.any_other_code1, d.branch), ("Access Bank", "044", "Lagos"))
        self.assertEqual(d.verification.account_name, "ADA OBI")
        self.assertEqual(out["bank_verified"], 1)
        self.assertTrue(hasattr(d, "bank_link"))

    def test_account_name_field_is_read_only_on_the_employee_form(self):
        from employee.forms import EmployeeBankDetailsUpdateForm

        emp = make_employee(company=self.company, email="f@x.test")
        d = EmployeeBankDetails.objects.create(employee_id=emp, bank_name="Access Bank",
                                               account_number="0123456789", any_other_code1="044", branch="Lagos")
        AccountVerification.objects.create(details=d, status="verified", account_number="0123456789",
                                           bank_code="044", account_name="ADA OBI", name_matches=True)
        f = EmployeeBankDetailsUpdateForm(instance=d)
        self.assertTrue(f.fields["account_name"].disabled)
        self.assertIn("ADA OBI", str(f["account_name"]))


class LookupAndKeyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.bank = Bank.objects.create(code="044", name="Access Bank")
        cls.su = admin()

    def get(self, **q):
        self.client.force_login(self.su)
        return self.client.get(reverse("bx-lookup"), q)

    def test_environment_variable_key_is_used_and_a_saved_key_wins(self):
        with mock.patch.dict("os.environ", {"PAYSTACK_SECRET_KEY": "sk_test_env"}):
            self.assertEqual(paystack.secret_key(), "sk_test_env")
            PaystackSettings.objects.create(secret_key="sk_test_saved")
            self.assertEqual(paystack.secret_key(), "sk_test_saved")
            PaystackSettings.objects.update(is_enabled=False)
            self.assertEqual(paystack.secret_key(), "")

    @mock.patch("bank_export.paystack.requests.get", side_effect=paystack_ok)
    def test_lookup_returns_the_account_name_before_anything_is_saved(self, get):
        with mock.patch.dict("os.environ", {"PAYSTACK_SECRET_KEY": "sk_test_env"}):
            r = self.get(account_number="0123456789", bank_code="044").json()
            self.assertEqual((r["status"], r["account_name"]), ("verified", "ADA OBI"))
            by_id = self.get(account_number="0123456789", bank_id=self.bank.pk).json()
            self.assertEqual(by_id["account_name"], "ADA OBI")
            bad = self.get(account_number="5555555555", bank_code="044").json()
            self.assertEqual(bad["status"], "failed")
            short = self.get(account_number="123", bank_code="044").json()
            self.assertEqual(short["status"], "idle")
        self.assertFalse(AccountVerification.objects.exists())     # a lookup saves nothing

    def test_lookup_needs_permission_and_a_key(self):
        u = make_user("plain2")
        u.is_new_employee = False
        u.save()
        make_employee(company=make_company("Other2"), email="p2@x.test", user=u)
        self.client.force_login(u)
        self.assertEqual(self.client.get(reverse("bx-lookup"), {"account_number": "0123456789", "bank_code": "044"}).status_code, 403)
        with mock.patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("PAYSTACK_SECRET_KEY", None)
            r = self.get(account_number="0123456789", bank_code="044").json()
        self.assertIn("not set up", r["message"])

    def test_paystack_is_listed_under_settings_payroll(self):
        from payroll.sidebar import PayrollSettings

        self.assertIn("Paystack", [str(i["label"]) for i in PayrollSettings.items])
