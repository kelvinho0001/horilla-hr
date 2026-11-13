"""
Bank dropdown for Horilla's employee bank form (Employee > Bank details).

Horilla's form has free-text Bank Name / Bank Code #1 / Branch boxes. When the bank list is loaded this turns
Bank Name into a dropdown of the list, fills Bank Code #1 from the chosen bank (in the browser as soon as it is
picked, and again on the server so it can never disagree with the list) and defaults Branch to Lagos
(setting BANK_DEFAULT_BRANCH). An old free-text bank name already on a record is kept as an extra
"not in the bank list" choice so nothing is lost. With an empty bank list Horilla's own text boxes are left alone.
"""

from django import forms
from django.conf import settings
from django.utils.translation import gettext_lazy as _

from .models import Bank

FILL_JS = (
    "var o=this.options[this.selectedIndex],c=document.getElementById('id_any_other_code1'),"
    "b=document.getElementById('id_branch');"
    "if(c&&o&&o.dataset.code){c.value=o.dataset.code;}"
    "if(b&&!b.value){b.value=%r;}"
)


def default_branch():
    return getattr(settings, "BANK_DEFAULT_BRANCH", "Lagos")


class BankSelect(forms.Select):
    """A select whose options carry the bank code (data-code) so the page can fill Bank Code #1."""

    codes = {}

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex=subindex, attrs=attrs)
        if value in self.codes:
            option["attrs"]["data-code"] = self.codes[value]
        return option


def apply_bank_dropdown(form):
    """Call at the end of an EmployeeBankDetails form's __init__."""
    if "bank_name" not in form.fields:
        return
    banks = list(Bank.objects.filter(is_active=True).order_by("name"))
    if not banks:
        return
    by_name = {b.name: b for b in banks}
    old = form.fields["bank_name"]
    current = ((form.instance.bank_name if form.instance is not None else "") or "").strip()
    choices = [("", _("---Choose Bank---"))] + [(b.name, b.name) for b in banks]
    if current and current not in by_name:
        choices.append((current, _("%(name)s (not in the bank list)") % {"name": current}))
    widget = BankSelect(attrs={"class": "oh-select w-100", "onchange": FILL_JS % default_branch()})
    widget.codes = {b.name: b.code for b in banks}
    form.fields["bank_name"] = forms.ChoiceField(
        choices=choices, label=old.label, required=old.required, widget=widget,
        help_text=_("Pick from the bank list. Bank Code #1 fills in by itself."),
    )
    if current:
        form.initial["bank_name"] = current

    code = form.fields.get("any_other_code1")
    if code is not None:
        code.required = False
        code.widget.attrs["readonly"] = True  # still submitted; the server sets it from the bank list anyway
        code.help_text = _("Filled from the bank you pick.")
    branch = form.fields.get("branch")
    if branch is not None:
        branch.required = False
        if not (form.instance is not None and form.instance.branch):
            branch.initial = default_branch()

    original_clean = form.clean

    def clean():
        data = original_clean() or form.cleaned_data
        bank = by_name.get(data.get("bank_name"))
        if bank is not None:
            data["any_other_code1"] = bank.code
        elif data.get("bank_name") and not data.get("any_other_code1") and "bank_name" not in form.errors:
            form.add_error("any_other_code1", _("Bank Code #1 is required for a bank that is not in the list."))
        if "branch" in form.fields and not data.get("branch"):
            data["branch"] = default_branch()
        return data

    form.clean = clean
    # Django caches a BoundField per field; the form's __init__ already touched them, so drop the stale ones
    # or the page and validation would keep using the original text box.
    form._bound_fields_cache = {}
