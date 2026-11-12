from django import forms
from django.utils.translation import gettext_lazy as _

from base.forms import ModelForm

from .models import PayrollRun


class RunForm(ModelForm):
    start_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    end_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))

    class Meta:
        model = PayrollRun
        fields = ["name", "start_date", "end_date", "default_working_days", "narration", "department", "employee_type"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Horilla's form base pre-fills date fields with today; a payroll period must be chosen on purpose.
        for f in ("start_date", "end_date"):
            self.fields[f].initial = None
        self.fields["narration"].required = False
        self.fields["narration"].help_text = _("Shown on the bank payment file. Leave empty for 'MONTH SALARY - COMPANY'.")
        self.fields["default_working_days"].help_text = _("Working days in the pay month. Can be changed per worker afterwards.")

    def clean(self):
        cd = super().clean()
        if cd.get("start_date") and cd.get("end_date") and cd["end_date"] < cd["start_date"]:
            raise forms.ValidationError(_("End date is before start date."))
        if cd.get("default_working_days") is not None and cd["default_working_days"] <= 0:
            self.add_error("default_working_days", _("Must be greater than zero."))
        return cd
