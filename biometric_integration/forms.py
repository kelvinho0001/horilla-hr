from django import forms
from django.utils.translation import gettext_lazy as _

from employee.models import Employee

from .models import PROVIDER_MANUAL_FILE, ExternalEmployeeMapping, ProviderConfig


class UploadForm(forms.Form):
    provider = forms.ModelChoiceField(
        queryset=ProviderConfig.objects.none(), label=_("Provider"), required=False
    )
    file = forms.FileField(label=_("Attendance report (.csv or .xlsx)"))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["provider"].queryset = ProviderConfig.objects.filter(
            provider_type=PROVIDER_MANUAL_FILE, is_active=True
        )

    def clean_file(self):
        f = self.cleaned_data["file"]
        if f.size > 10 * 1024 * 1024:
            raise forms.ValidationError(_("File is larger than 10 MB."))
        if not f.name.lower().endswith((".csv", ".txt", ".xlsx", ".xlsm")):
            raise forms.ValidationError(_("Upload a .csv or .xlsx file."))
        return f


class MappingForm(forms.Form):
    provider = forms.ModelChoiceField(queryset=ProviderConfig.objects.filter(is_active=True))
    employee = forms.ModelChoiceField(queryset=Employee.objects.filter(is_active=True))
    external_employee_id = forms.CharField(max_length=100, label=_("Biometric user ID"))
    status = forms.ChoiceField(choices=ExternalEmployeeMapping.STATUS_CHOICES, initial="verified")

    def clean_external_employee_id(self):
        return self.cleaned_data["external_employee_id"].strip()
