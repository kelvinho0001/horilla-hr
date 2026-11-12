"""
Payroll now reads Horilla's own data: basic salary from the employee record, days from validated
Attendance, and loans / salary advances / fines from Horilla's LoanAccount. The separate pay-profile,
fine and loan-agreement tables are removed. Instalments that approved runs already collected are carried
across to the new ledger so they are never taken twice.
"""

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


def carry_over_collected_instalments(apps, schema_editor):
    Old = apps.get_model("weekly_payroll", "LoanRepayment")
    New = apps.get_model("weekly_payroll", "LoanDeduction")
    for r in Old.objects.filter(kind="instalment", installment_no__isnull=False, run__isnull=False).select_related("agreement"):
        New.objects.get_or_create(
            loan_id=r.agreement.loan_id, installment_no=r.installment_no,
            defaults={"due_date": r.due_date or r.created_at.date(), "amount": r.amount, "run_id": r.run_id},
        )


class Migration(migrations.Migration):

    dependencies = [
        ("base", "0021_penaltyaccounts_additional_info_and_more"),
        ("payroll", "0015_systemcomponenttaxoverride"),
        ("weekly_payroll", "0002_alter_payrollrun_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="payrollrun",
            name="employee_type",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                to="base.employeetype", verbose_name="Worker category",
            ),
        ),
        migrations.CreateModel(
            name="LoanDeduction",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("installment_no", models.PositiveIntegerField()),
                ("due_date", models.DateField()),
                ("amount", models.DecimalField(decimal_places=2, max_digits=12)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("loan", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="payroll_deductions", to="payroll.loanaccount")),
                ("run", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="loan_deductions", to="weekly_payroll.payrollrun")),
            ],
            options={"ordering": ["loan_id", "installment_no"]},
        ),
        migrations.AddConstraint(
            model_name="loandeduction",
            constraint=models.UniqueConstraint(fields=("loan", "installment_no"), name="uniq_loan_installment_once"),
        ),
        migrations.RunPython(carry_over_collected_instalments, migrations.RunPython.noop),
        migrations.DeleteModel(name="Fine"),
        migrations.DeleteModel(name="FineType"),
        migrations.DeleteModel(name="LoanRepayment"),
        migrations.DeleteModel(name="LoanAgreement"),
        migrations.DeleteModel(name="WorkerPayProfile"),
    ]
