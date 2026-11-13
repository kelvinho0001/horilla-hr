from django.test import TestCase

from bank_export import services
from bank_export.models import Bank
from employee.forms import EmployeeBankDetailsForm, EmployeeBankDetailsUpdateForm
from employee.models import EmployeeBankDetails
from horilla.testkit import make_company, make_employee


class BankDropdownTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.access = Bank.objects.create(code="044", name="Access Bank")
        cls.alat = Bank.objects.create(code="035A", name="ALAT by WEMA")
        Bank.objects.create(code="999", name="Closed Bank", is_active=False)
        cls.emp = make_employee(company=make_company(), email="bank@x.test")

    def data(self, **kw):
        base = {"bank_name": "Access Bank", "account_number": "0123456789", "branch": "", "any_other_code1": "",
                "address": "1 Road", "country": "", "state": "", "city": "", "any_other_code2": ""}
        base.update(kw)
        return base

    def test_bank_name_becomes_a_dropdown_of_active_banks_carrying_their_codes(self):
        html = str(EmployeeBankDetailsForm()["bank_name"])
        self.assertIn("<select", html)
        self.assertIn('data-code="044"', html)
        self.assertIn('data-code="035A"', html)
        self.assertNotIn("Closed Bank", html)
        self.assertIn("onchange", html)                        # fills Bank Code #1 and Branch in the browser

    def test_server_fills_code_and_branch_even_if_left_empty_or_tampered(self):
        for tampered in ("", "000", "999"):
            f = EmployeeBankDetailsForm(self.data(any_other_code1=tampered))
            self.assertTrue(f.is_valid(), f.errors)
            self.assertEqual((f.cleaned_data["any_other_code1"], f.cleaned_data["branch"]), ("044", "Lagos"))

    def test_saved_record_gets_code_and_branch_and_matches_the_bank_list_exactly(self):
        f = EmployeeBankDetailsForm(self.data(bank_name="ALAT by WEMA", account_number="0000000042"))
        self.assertTrue(f.is_valid(), f.errors)
        d = f.save(commit=False)
        d.employee_id = self.emp
        d.save()
        d.refresh_from_db()
        self.assertEqual((d.bank_name, d.any_other_code1, d.branch), ("ALAT by WEMA", "035A", "Lagos"))
        bank, acct, issues = services.resolve_bank(self.emp)
        self.assertEqual((bank, acct, issues), (self.alat, "0000000042", []))

    def test_a_branch_typed_by_hand_is_kept(self):
        f = EmployeeBankDetailsForm(self.data(branch="Ikeja"))
        self.assertTrue(f.is_valid(), f.errors)
        self.assertEqual(f.cleaned_data["branch"], "Ikeja")

    def test_a_bank_that_is_not_in_the_list_is_refused(self):
        f = EmployeeBankDetailsForm(self.data(bank_name="Made Up Bank"))
        self.assertFalse(f.is_valid())
        self.assertIn("bank_name", f.errors)

    def test_old_free_text_bank_is_kept_as_a_choice_and_not_lost(self):
        d = EmployeeBankDetails.objects.create(employee_id=self.emp, bank_name="GTBank", account_number="0123456789", any_other_code1="058", branch="Yaba")
        f = EmployeeBankDetailsForm(self.data(bank_name="GTBank", any_other_code1="058", branch="Yaba"), instance=d)
        self.assertIn("GTBank (not in the bank list)", str(f["bank_name"]))
        self.assertTrue(f.is_valid(), f.errors)
        self.assertEqual(f.cleaned_data["any_other_code1"], "058")
        # and without a code a non-list bank needs one
        f2 = EmployeeBankDetailsForm(self.data(bank_name="GTBank", any_other_code1=""), instance=d)
        self.assertFalse(f2.is_valid())
        self.assertIn("any_other_code1", f2.errors)

    def test_the_other_bank_form_behaves_the_same(self):
        f = EmployeeBankDetailsUpdateForm(self.data(bank_name="ALAT by WEMA", account_number="0000000043"))
        self.assertIn('data-code="035A"', str(f["bank_name"]))
        self.assertTrue(f.is_valid(), f.errors)
        self.assertEqual((f.cleaned_data["any_other_code1"], f.cleaned_data["branch"]), ("035A", "Lagos"))

    def test_without_a_bank_list_horillas_own_text_boxes_are_left_alone(self):
        Bank.objects.all().delete()
        html = str(EmployeeBankDetailsForm()["bank_name"])
        self.assertIn("<input", html)
        self.assertNotIn("<select", html)

    def test_default_branch_is_a_setting(self):
        from django.test import override_settings

        with override_settings(BANK_DEFAULT_BRANCH="Abuja"):
            f = EmployeeBankDetailsForm(self.data())
            self.assertTrue(f.is_valid(), f.errors)
            self.assertEqual(f.cleaned_data["branch"], "Abuja")
