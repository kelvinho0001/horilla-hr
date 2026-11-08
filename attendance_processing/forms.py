from django import forms
from django.utils.translation import gettext_lazy as _

from .models import AttendanceRule


class RuleForm(forms.ModelForm):
    window_start = forms.TimeField(required=False, widget=forms.TimeInput(attrs={"type": "time"}))
    window_end = forms.TimeField(required=False, widget=forms.TimeInput(attrs={"type": "time"}))

    class Meta:
        model = AttendanceRule
        fields = [
            "name", "scope", "department", "shift", "employee_type", "employee",
            "window_start", "window_end", "working_weekdays", "is_active",
        ]
