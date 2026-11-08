import io
from datetime import date, timedelta
from decimal import Decimal

import openpyxl
from django.contrib.auth.models import Group
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from attendance_processing import permissions as att_perms
from bank_export import permissions, services
from bank_export.models import Bank, ExportBatch, PaymentTemplate
from base.models import CompanyGroupAssignment
from employee.models import EmployeeBankDetails
from horilla.testkit import make_employee
from weekly_payroll import permissions as pay_perms
from weekly_payroll import services as pay
from weekly_payroll.models import PayrollRun
from weekly_payroll.tests.test_payroll import Base as PayBase, next_n

D = Decimal


class Base(PayBase):
    def setUp(self):
        self.access = Bank.objects.create(code="044", name="Access Bank")
        self.alat = Bank.objects.create(code="035A", name="ALAT by WEMA")

    def paid_worker(self, basic="70000", days=28, acct=None, bank=None, with_bank=True):
        e = self.worker(basic)
        self.present(e, days)
        if with_bank:
            services.save_worker_bank(e, bank or self.access, acct or f"{next_n():010d}")
        return e

    def approved_run(self, **kw):
        r = self.mkrun(**kw)
        pay.mark_reviewed(r, self.user())
        pay.approve(r, self.user())
        return PayrollRun.objects.get(pk=r.pk)

    def sheet(self, batch, name):
        return openpyxl.load_workbook(io.BytesIO(bytes(batch.content)))[name]


class BankDirectoryTests(TestCase):
    def test_codes_stay_text_and_upload_reports(self):
        csv = b"CODE,NAME\n044,Access Bank\n035A,ALAT by WEMA\n120001,9mobile\n,NoCode\n!!,Bad\n044,Access Bank Plc\n"
        rows, errors = services.load_bank_rows(io.BytesIO(csv), "b.csv")
        self.assertEqual(len(errors), 2)
        res = services.import_banks(rows)
        self.assertEqual((res["created"], res["duplicates_in_file"]), (3, 1))
        self.assertEqual(Bank.objects.get(code="044").name, "Access Bank Plc")
        res2 = services.import_banks(rows)
        self.assertEqual((res2["created"], res2["updated"]), (0, 3))  # re-upload is safe

    def test_missing_columns_rejected(self):
        with self.assertRaises(services.ExportError):
            services.load_bank_rows(io.BytesIO(b"A,B\n1,2\n"), "b.csv")

    def test_excel_upload(self):
        wb = openpyxl.Workbook()
        wb.active.append(["CODE", "NAME"])
        wb.active.append(["044", "Access Bank"])
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        rows, _ = services.load_bank_rows(buf, "b.xlsx")
        self.assertEqual(rows, [("044", "Access Bank")])

    def test_shipped_csv_loads(self):
        with open("docs/demiller/bank_codes.csv", "rb") as f:
            rows, errors = services.load_bank_rows(f, "bank_codes.csv")
        self.assertEqual((len(rows), errors), (87, []))
        self.assertIn(("044", "Access Bank"), rows)


class AccountTests(Base):
    def test_save_populates_horilla_record_and_code_comes_from_directory(self):
        e = self.worker()
        services.save_worker_bank(e, self.access, "0123456789")
        d = EmployeeBankDetails.objects.get(employee_id=e)
        self.assertEqual((d.account_number, d.bank_name, d.any_other_code1), ("0123456789", "Access Bank", "044"))
        bank, acct, issues = services.resolve_bank(e)
        self.assertEqual((bank, acct, issues), (self.access, "0123456789", []))

    def test_invalid_and_duplicate_accounts_refused(self):
        a, b = self.worker(), self.worker()
        for bad in ("123", "12345678AB", ""):
            with self.assertRaises(services.ExportError):
                services.save_worker_bank(a, self.access, bad)
        services.save_worker_bank(a, self.access, "0123456789")
        with self.assertRaises(services.ExportError):
            services.save_worker_bank(b, self.access, "0123456789")

    def test_existing_horilla_record_resolves_by_code_or_name(self):
        e1, e2, e3 = self.worker(), self.worker(), self.worker()
        EmployeeBankDetails.objects.create(employee_id=e1, bank_name="whatever", account_number="0000000001", any_other_code1="035A")
        EmployeeBankDetails.objects.create(employee_id=e2, bank_name="access bank", account_number="0000000002")
        EmployeeBankDetails.objects.create(employee_id=e3, bank_name="Unknown Bank", account_number="0000000003")
        self.assertEqual(services.resolve_bank(e1)[0], self.alat)
        self.assertEqual(services.resolve_bank(e2)[0], self.access)
        self.assertIn("Bank not selected from the bank list", services.resolve_bank(e3)[2])

    def test_no_details_and_bad_account_flagged(self):
        e = self.worker()
        self.assertEqual(services.resolve_bank(e)[2], ["No bank details"])
        EmployeeBankDetails.objects.create(employee_id=e, bank_name="Access Bank", account_number="12ab")
        self.assertTrue(any("digits" in i for i in services.resolve_bank(e)[2]))

    def test_mask(self):
        self.assertEqual(services.mask("0123456789"), "••••••6789")


class ExportTests(Base):
    def test_only_approved_payroll_is_exported(self):
        self.paid_worker()
        r = self.mkrun()
        for status in ("draft", "reviewed"):
            PayrollRun.objects.filter(pk=r.pk).update(status=status)
            with self.assertRaises(services.ExportError):
                services.export_run(PayrollRun.objects.get(pk=r.pk), self.user())

    def test_workbook_sheets_leading_zeros_and_totals(self):
        a = self.paid_worker("70000", 14, "0123456789")
        b = self.paid_worker("55000", 28, "0000000042", bank=self.alat)
        run = self.approved_run()
        batch = services.export_run(run, self.user())
        ws = self.sheet(batch, "PAYMENT SCHEDULE")
        self.assertEqual(
            [c.value for c in ws[1]],
            ["EMP ID", "FIRST NAME", "LAST NAME", "DEPARTMENT", "ACCOUNT NUMBER", "BENEFICIARY NAME", "AMOUNT", "BANK CODE", "NARRATION"],
        )
        rows = {r[0].value: [c.value for c in r] for r in ws.iter_rows(min_row=2)}
        ra = rows[a.badge_id]
        self.assertEqual((ra[4], ra[7]), ("0123456789", "044"))
        rb = rows[b.badge_id]
        self.assertEqual((rb[4], rb[7]), ("0000000042", "035A"))
        self.assertEqual(ra[6], 35000.0)
        self.assertEqual(rb[5], f"{b.employee_first_name} {b.employee_last_name}".upper())
        self.assertEqual(ra[8], "OCTOBER SALARY - DEMILLER FARMS")
        self.assertEqual(ws["E2"].data_type, "s")
        total = sum(r[6] for r in rows.values())
        self.assertEqual(D(str(total)), batch.total_amount)
        self.assertEqual(batch.total_amount, run.total_net)           # export total matches payroll
        self.assertEqual(batch.employee_count, run.employee_count)
        self.assertEqual({r[0].value for r in self.sheet(batch, "BANK CODES").iter_rows(min_row=2)}, {"044", "035A"})
        self.assertIn("Table1", self.sheet(batch, "PAYROLL").tables)
        self.assertIn("Table3", ws.tables)
        info = self.sheet(batch, "RUN INFO")
        self.assertEqual(info["B1"].value, run.name)
        run.refresh_from_db()
        self.assertEqual(run.status, "exported")

    def test_missing_bank_details_block_unless_partial(self):
        ok = self.paid_worker()
        bad = self.paid_worker(with_bank=False)
        run = self.approved_run()
        pf = services.preflight(run)
        self.assertEqual((pf["count"], len(pf["blocking"])), (1, 1))
        with self.assertRaises(services.ExportError) as cm:
            services.export_run(run, self.user())
        self.assertEqual(len(cm.exception.problems), 1)
        self.assertFalse(ExportBatch.objects.exists())
        batch = services.export_run(run, self.user(), allow_partial=True)
        self.assertEqual(batch.employee_count, 1)
        self.assertEqual(len(batch.excluded), 1)
        self.assertEqual(batch.total_amount, pay.PayrollLine.objects.get(run=run, employee=ok).amount_earned)

    def test_zero_or_negative_net_not_paid(self):
        e = self.paid_worker(days=0)
        self.paid_worker()
        pf = services.preflight(self.approved_run())
        self.assertEqual(pf["count"], 1)
        self.assertFalse(pf["blocking"])

    def test_duplicate_export_prevented_and_db_enforced(self):
        self.paid_worker()
        run = self.approved_run()
        batch = services.export_run(run, self.user())
        run.refresh_from_db()
        with self.assertRaises(services.ExportError):
            services.export_run(run, self.user())
        with self.assertRaises(IntegrityError), transaction.atomic():
            ExportBatch.objects.create(
                run=run, reference="X", narration="n", employee_count=1, total_amount=1, content=b"", sha256="x"
            )
        self.assertEqual(ExportBatch.objects.count(), 1)

    def test_supersede_needs_reason_then_regenerates_with_new_reference(self):
        self.paid_worker()
        run = self.approved_run()
        first = services.export_run(run, self.user())
        with self.assertRaises(services.ExportError):
            services.supersede(first, self.user(), "")
        services.supersede(first, self.user(), "Wrong narration")
        run.refresh_from_db()
        self.assertEqual(run.status, "approved")
        second = services.export_run(run, self.user())
        self.assertNotEqual(first.reference, second.reference)
        self.assertEqual(list(ExportBatch.objects.filter(run=run).values_list("status", flat=True).order_by("id")), ["superseded", "generated"])

    def test_paid_is_terminal(self):
        self.paid_worker()
        run = self.approved_run()
        b = services.export_run(run, self.user())
        services.mark_paid(b, self.user())
        run.refresh_from_db()
        self.assertEqual(run.status, "paid")
        with self.assertRaises(services.ExportError):
            services.supersede(b, self.user(), "oops")
        with self.assertRaises(services.ExportError):
            services.mark_paid(b, self.user())
        with self.assertRaises(pay.PayrollError):
            pay.generate(run)

    def test_exported_payroll_cannot_be_recalculated_or_overlapped(self):
        self.paid_worker()
        run = self.approved_run()
        services.export_run(run, self.user())
        run.refresh_from_db()
        with self.assertRaises(pay.PayrollError):
            pay.generate(run)
        r2 = self.mkrun(start=date(2024, 10, 20), end=date(2024, 11, 5), name="Overlap")
        pay.mark_reviewed(r2, self.user())
        with self.assertRaises(pay.PayrollError):
            pay.approve(r2, self.user())

    def test_custom_column_order_and_headers(self):
        PaymentTemplate.objects.all().update(is_default=False)
        PaymentTemplate.objects.create(name="Other", is_default=True, columns=[
            {"field": "bank_code", "header": "Sort Code"}, {"field": "account_number", "header": "Acct"},
            {"field": "amount", "header": "Amt"}, {"field": "narration", "header": "Ref"},
        ])
        self.paid_worker()
        batch = services.export_run(self.approved_run(), self.user())
        ws = self.sheet(batch, "PAYMENT SCHEDULE")
        self.assertEqual([c.value for c in ws[1]], ["Sort Code", "Acct", "Amt", "Ref"])
        self.assertEqual(ws["A2"].value, "044")

    def test_default_template_seeded_matching_demiller_sheet(self):
        t = PaymentTemplate.objects.get(is_default=True)
        self.assertEqual([c["header"] for c in t.columns][4:8], ["ACCOUNT NUMBER", "BENEFICIARY NAME", "AMOUNT", "BANK CODE"])


class RoleViewTests(Base):
    def setUp(self):
        super().setUp()
        for m in (att_perms, pay_perms, permissions):
            m.sync_groups()
        self.paid_worker()
        self.run_ = self.approved_run()

    def login(self, group):
        user = make_employee(company=self.company, email=f"{next_n()}@x.test").employee_user_id
        user.is_new_employee = False
        user.save()
        CompanyGroupAssignment.objects.create(user=user, company=self.company, group=Group.objects.get(name=group))
        self.client.force_login(user)
        return user

    def test_only_accountant_generates_officer_cannot(self):
        self.login("HR Officer")
        self.client.post(reverse("bx-generate", args=[self.run_.pk]))
        self.assertEqual(ExportBatch.objects.count(), 0)
        self.assertNotEqual(self.client.get(reverse("bx-dashboard")).status_code, 200)
        self.login("Accountant")
        self.client.post(reverse("bx-generate", args=[self.run_.pk]))
        self.assertEqual(ExportBatch.objects.count(), 1)

    def test_download_and_history_pages(self):
        self.login("Accountant")
        self.client.post(reverse("bx-generate", args=[self.run_.pk]))
        b = ExportBatch.objects.get()
        r = self.client.get(reverse("bx-download", args=[b.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertIn(b.reference, r["Content-Disposition"])
        wb = openpyxl.load_workbook(io.BytesIO(r.content))
        self.assertIn("PAYMENT SCHEDULE", wb.sheetnames)
        b.refresh_from_db()
        self.assertEqual(b.download_count, 1)
        for name, args in (("bx-dashboard", []), ("bx-run", [self.run_.pk]), ("bx-banks", []), ("bx-accounts", [])):
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 200, name)

    def test_supersede_is_approver_only_and_pay_is_accountant(self):
        self.login("Accountant")
        self.client.post(reverse("bx-generate", args=[self.run_.pk]))
        b = ExportBatch.objects.get()
        self.client.post(reverse("bx-supersede", args=[b.pk]), {"reason": "x"})
        b.refresh_from_db()
        self.assertEqual(b.status, "generated")
        self.login("HR/Payroll Approver")
        self.client.post(reverse("bx-supersede", args=[b.pk]), {"reason": "typo"})
        b.refresh_from_db()
        self.assertEqual(b.status, "superseded")

    def test_bank_edit_roles_and_masking(self):
        e = self.worker()
        self.login("Accountant")
        self.assertEqual(self.client.get(reverse("bx-account-edit", args=[e.pk])).status_code, 302)
        r = self.client.get(reverse("bx-accounts"))
        self.assertNotContains(r, "0123456789")
        self.login("HR Officer")
        r = self.client.post(reverse("bx-account-edit", args=[e.pk]), {
            "employee": e.pk, "bank": self.alat.pk, "account_number": "0999999999",
        })
        self.assertEqual(r.status_code, 302)
        self.assertEqual(services.resolve_bank(e)[0], self.alat)

    def test_accountant_uploads_bank_list(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        self.login("Accountant")
        self.client.post(reverse("bx-banks"), {"file": SimpleUploadedFile("b.csv", b"CODE,NAME\n058,GTBank\n")})
        self.assertTrue(Bank.objects.filter(code="058").exists())
