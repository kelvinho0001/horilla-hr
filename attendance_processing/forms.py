from datetime import time

from django import forms
from django.forms import inlineformset_factory
from django.utils.translation import gettext_lazy as _

from base.forms import ModelForm

from .models import AttendanceRule, AttendanceRuleWindow

TIME = {"type": "time", "class": "oh-input w-100"}


class RuleForm(ModelForm):
    class Meta:
        model = AttendanceRule
        fields = ["name", "scope", "department", "shift", "employee_type", "employee", "is_active"]


class BaseWindowFormSet(forms.BaseInlineFormSet):
    def clean(self):
        super().clean()
        spans = []
        for f in self.forms:
            cd = getattr(f, "cleaned_data", None)
            if not cd or cd.get("DELETE") or not cd.get("start") or not cd.get("end"):
                continue
            if cd["start"] == cd["end"]:
                raise forms.ValidationError(_("A window cannot start and end at the same time."))
            # An overnight window (19:00-09:00) is the two pieces 19:00-23:59:59 and 00:00-09:00.
            a, b = cd["start"], cd["end"]
            pieces = [(a, b)] if a < b else [(a, time(23, 59, 59, 999999)), (time(0, 0), b)]
            for pa, pb in pieces:
                for sa, sb in spans:
                    if pa <= sb and sa <= pb:
                        raise forms.ValidationError(_("Windows must not overlap."))
            spans.extend(pieces)


WindowFormSet = inlineformset_factory(
    AttendanceRule, AttendanceRuleWindow, formset=BaseWindowFormSet, fields=("start", "end"),
    extra=2, can_delete=True,
    widgets={"start": forms.TimeInput(attrs=TIME, format="%H:%M"), "end": forms.TimeInput(attrs=TIME, format="%H:%M")},
)
