from django import forms
from django.forms import inlineformset_factory
from django.utils.translation import gettext_lazy as _

from .models import AttendanceRule, AttendanceRuleWindow


class RuleForm(forms.ModelForm):
    class Meta:
        model = AttendanceRule
        fields = [
            "name", "scope", "department", "shift", "employee_type", "employee",
            "working_weekdays", "is_active",
        ]


class BaseWindowFormSet(forms.BaseInlineFormSet):
    def clean(self):
        super().clean()
        spans = []
        for f in self.forms:
            cd = getattr(f, "cleaned_data", None)
            if not cd or cd.get("DELETE") or not cd.get("start") or not cd.get("end"):
                continue
            for a, b in spans:
                if cd["start"] <= b and a <= cd["end"]:
                    raise forms.ValidationError(_("Windows must not overlap."))
            spans.append((cd["start"], cd["end"]))


WindowFormSet = inlineformset_factory(
    AttendanceRule, AttendanceRuleWindow, formset=BaseWindowFormSet, fields=("start", "end"),
    extra=2, can_delete=True,
    widgets={"start": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
             "end": forms.TimeInput(attrs={"type": "time"}, format="%H:%M")},
)
