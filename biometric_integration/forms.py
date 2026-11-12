from django import forms

from base.forms import Form, ModelForm
from django.utils.translation import gettext_lazy as _

from employee.models import Employee

from .models import PROVIDER_MANUAL_FILE, ExternalEmployeeMapping, ProviderConfig


class UploadForm(Form):
    provider = forms.ModelChoiceField(
        queryset=ProviderConfig.objects.none(), label=_("Provider"), required=False
    )
    file = forms.FileField(label=_("Attendance report (.csv, .xlsx, or the device's .dat file)"))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["provider"].queryset = ProviderConfig.objects.filter(
            provider_type=PROVIDER_MANUAL_FILE, is_active=True
        )

    def clean_file(self):
        f = self.cleaned_data["file"]
        if f.size > 10 * 1024 * 1024:
            raise forms.ValidationError(_("File is larger than 10 MB."))
        if not f.name.lower().endswith((".csv", ".txt", ".xlsx", ".xlsm", ".dat")):
            raise forms.ValidationError(_("Upload a .csv, .xlsx or .dat file."))
        return f


class MappingForm(Form):
    provider = forms.ModelChoiceField(queryset=ProviderConfig.objects.filter(is_active=True))
    employee = forms.ModelChoiceField(queryset=Employee.objects.filter(is_active=True))
    external_employee_id = forms.CharField(max_length=100, label=_("Biometric user ID"))
    status = forms.ChoiceField(choices=ExternalEmployeeMapping.STATUS_CHOICES, initial="verified")

    def clean_external_employee_id(self):
        return self.cleaned_data["external_employee_id"].strip()


class WorkerSheetForm(Form):
    user_file = forms.FileField(label=_("Device user list (user.dat)"))
    timenet_file = forms.FileField(
        required=False, label=_("Time.Net employee export (optional, .csv or .xlsx)"),
        help_text=_("Gives full names and the department. Needs an ID column and name columns."),
    )
    provider = forms.ModelChoiceField(
        queryset=ProviderConfig.objects.none(), required=False, label=_("Device (to compare with its punches)"),
    )
    company = forms.ModelChoiceField(queryset=None, label=_("Company"))
    exclude_ids = forms.CharField(
        required=False, label=_("IDs to leave out (comma-separated)"),
        help_text=_("Admin or office staff who are not payroll workers."),
    )
    job_position = forms.CharField(required=False, label=_("Job position (for everyone)"))
    job_role = forms.CharField(required=False, label=_("Job role (for everyone)"))
    shift = forms.CharField(required=False, label=_("Shift (optional)"))
    work_type = forms.CharField(required=False, label=_("Work type (optional)"))
    employee_type = forms.CharField(required=False, initial="Casual", label=_("Worker category / employee type"))
    location = forms.CharField(required=False)
    date_joining = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}),
                                   label=_("Date joining (optional; attendance before it is ignored)"))
    email_domain = forms.CharField(initial="workers.invalid", label=_("Placeholder e-mail domain"),
                                   help_text=_("Workers without an e-mail get id@this-domain. Replace later if needed."))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from base.models import Company

        self.fields["company"].queryset = Company.objects.all()
        self.fields["provider"].queryset = ProviderConfig.objects.filter(is_active=True)
