from django import forms
from django.utils.translation import gettext_lazy as _

from .models import Fine, FineType, LoanAgreement, PayrollRun, WorkerPayProfile
from payroll.models.models import LoanAccount


class RunForm(forms.ModelForm):
    start_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    end_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))

    class Meta:
        model = PayrollRun
        fields = ["name", "start_date", "end_date", "default_working_days", "narration", "department"]

    def clean(self):
        cd = super().clean()
        if cd.get("start_date") and cd.get("end_date") and cd["end_date"] < cd["start_date"]:
            raise forms.ValidationError(_("End date is before start date."))
        if cd.get("default_working_days") is not None and cd["default_working_days"] <= 0:
            self.add_error("default_working_days", _("Must be greater than zero."))
        return cd


class ProfileForm(forms.ModelForm):
    effective_from = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))

    class Meta:
        model = WorkerPayProfile
        fields = ["employee", "basic_salary", "effective_from", "note"]


class FineForm(forms.ModelForm):
    date_issued = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))

    class Meta:
        model = Fine
        fields = ["employee", "fine_type", "description", "amount", "date_issued"]


class FineTypeForm(forms.ModelForm):
    class Meta:
        model = FineType
        fields = ["name", "default_amount", "is_active"]


class AgreementForm(forms.ModelForm):
    first_due_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))

    class Meta:
        model = LoanAgreement
        fields = ["loan", "frequency", "instalment_amount", "first_due_date"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["loan"].queryset = LoanAccount.objects.filter(
            agreement__isnull=True, type__in=["loan", "advanced_salary"]
        )
