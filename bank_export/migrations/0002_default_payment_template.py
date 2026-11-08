from django.db import migrations

COLUMNS = [
    {"field": "emp_id", "header": "EMP ID"},
    {"field": "first_name", "header": "FIRST NAME"},
    {"field": "last_name", "header": "LAST NAME"},
    {"field": "department", "header": "DEPARTMENT"},
    {"field": "account_number", "header": "ACCOUNT NUMBER"},
    {"field": "beneficiary_name", "header": "BENEFICIARY NAME"},
    {"field": "amount", "header": "AMOUNT"},
    {"field": "bank_code", "header": "BANK CODE"},
    {"field": "narration", "header": "NARRATION"},
]


def seed(apps, schema_editor):
    T = apps.get_model("bank_export", "PaymentTemplate")
    T.objects.get_or_create(
        name="Demiller default (all banks)", defaults={"is_default": True, "columns": COLUMNS}
    )


class Migration(migrations.Migration):
    dependencies = [("bank_export", "0001_initial")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
