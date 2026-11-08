from django import forms
from django.utils.translation import gettext_lazy as _

from employee.models import Employee

from .models import Bank


class BankUploadForm(forms.Form):
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


class WorkerBankForm(forms.Form):
    employee = forms.ModelChoiceField(queryset=Employee.objects.filter(is_active=True))
    bank = forms.ModelChoiceField(queryset=Bank.objects.filter(is_active=True), empty_label=_("Select bank"))
    account_number = forms.CharField(max_length=20)
