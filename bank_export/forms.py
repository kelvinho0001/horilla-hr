from django import forms

from base.forms import Form
from django.utils.translation import gettext_lazy as _

from employee.models import Employee

from .models import Bank


class BankUploadForm(Form):
    file = forms.FileField(
        label=_("Bank list (.csv or .xlsx with CODE and NAME columns)"),
        help_text=_("Keep codes as text: Excel turns 044 into 44 unless the column is formatted as Text."),
    )

    def clean_file(self):
        f = self.cleaned_data["file"]
        if not f.name.lower().endswith((".csv", ".xlsx", ".xlsm")):
            raise forms.ValidationError(_("Upload a .csv or .xlsx file."))
        if f.size > 2 * 1024 * 1024:
            raise forms.ValidationError(_("File is too large."))
        return f


class WorkerBankForm(Form):
    employee = forms.ModelChoiceField(queryset=Employee.objects.filter(is_active=True))
    bank = forms.ModelChoiceField(queryset=Bank.objects.filter(is_active=True), empty_label=_("Select bank"))
    account_number = forms.CharField(max_length=20)


class PaystackForm(Form):
    secret_key = forms.CharField(
        label=_("Paystack secret key"), required=False, strip=True,
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "off", "placeholder": "sk_live_…"}),
        help_text=_("From the Paystack dashboard > Settings > API Keys. Leave empty to keep the saved key."),
    )
    is_enabled = forms.BooleanField(label=_("Check bank accounts with Paystack"), required=False)
    remove_key = forms.BooleanField(label=_("Remove the saved key"), required=False)

    def clean_secret_key(self):
        k = self.cleaned_data["secret_key"]
        if k and not k.startswith(("sk_live_", "sk_test_")):
            raise forms.ValidationError(_("This is not a Paystack secret key (it starts with sk_live_ or sk_test_)."))
        return k
