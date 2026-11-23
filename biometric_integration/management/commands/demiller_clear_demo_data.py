"""
Clear demo/test DATA so real data can be entered, keeping the SETUP.

    python manage.py demiller_clear_demo_data          # dry run: shows what would be removed, changes nothing
    python manage.py demiller_clear_demo_data --yes    # really removes it (all or nothing)

Removed: workers (employees and their login accounts), biometric punches/imports/mappings/devices, all
attendance records, payroll runs and the loan ledger, bank payment files, Horilla loans/advances/fines,
Horilla payslips and batches, and the workers' contracts and bank details.

Kept: the company, departments, job positions, worker categories, shifts, work types, holidays, leave types,
roles/groups and who holds them, attendance rules, the bank list, the payment template, mail server
settings, and the people you keep (every superuser, everyone who holds a role group, plus --keep-badges).

Runs in ONE database transaction: if anything blocks, nothing is changed. Back up first
(scripts/backup.sh). This cannot be undone.
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import ProtectedError

KEEP_BLOCK_MODELS = ("Employee", "EmployeeWorkInformation")


class Command(BaseCommand):
    help = "Remove demo data (workers, punches, attendance, payroll, loans) and keep the setup."

    def add_arguments(self, parser):
        parser.add_argument("--yes", action="store_true", help="Actually delete (without it nothing is changed).")
        parser.add_argument("--keep-badges", default="", help="Comma-separated badge IDs of employees to keep as well.")

    # ---------------------------------------------------------------- plan
    def kept_employee_ids(self, badges):
        from base.models import CompanyGroupAssignment
        from employee.models import Employee

        keep = set(Employee.objects.entire().filter(employee_user_id__is_superuser=True).values_list("pk", flat=True))
        keep |= set(
            Employee.objects.entire().filter(
                employee_user_id__in=CompanyGroupAssignment.objects.values("user")
            ).values_list("pk", flat=True)
        )
        wanted = [b.strip() for b in badges.split(",") if b.strip()]
        if wanted:
            found = set(Employee.objects.entire().filter(badge_id__in=wanted).values_list("badge_id", flat=True))
            missing = sorted(set(wanted) - found)
            if missing:
                raise CommandError(f"--keep-badges: no employee with badge ID {', '.join(missing)}")
            keep |= set(Employee.objects.entire().filter(badge_id__in=wanted).values_list("pk", flat=True))
        return keep

    def models(self):
        from attendance.models import Attendance, AttendanceActivity, WorkRecords
        from bank_export.models import ExportBatch
        from base.models import PenaltyAccounts
        from biometric_integration.models import (
            ExternalEmployeeMapping, IgnoredExternalID, ImportBatch, ProviderConfig, RawTransaction,
        )
        from payroll.models.models import LoanAccount, Payslip
        from payroll.models.payroll_batch import PayrollBatch
        from weekly_payroll.models import LoanDeduction, PayrollRun

        # Order matters: children before the things they point at.
        return [
            ("bank payment files", ExportBatch), ("payroll loan ledger", LoanDeduction), ("payroll runs", PayrollRun),
            ("Horilla payslips", Payslip), ("Horilla payroll batches", PayrollBatch),
            ("Horilla loans / advances / fines", LoanAccount), ("penalty records", PenaltyAccounts),
            ("attendance records", Attendance), ("attendance activity", AttendanceActivity), ("work records", WorkRecords),
            ("biometric punches", RawTransaction), ("biometric imports", ImportBatch),
            ("biometric ID mappings", ExternalEmployeeMapping), ("ignored biometric IDs", IgnoredExternalID),
            ("biometric devices/sources", ProviderConfig),
        ]

    # -------------------------------------------------------------- handle
    def handle(self, *args, **options):
        from employee.models import Employee

        user_model = get_user_model()
        keep = self.kept_employee_ids(options["keep_badges"])
        targets = Employee.objects.entire().exclude(pk__in=keep)
        target_user_ids = list(targets.exclude(employee_user_id__isnull=True).values_list("employee_user_id", flat=True))
        execute = options["yes"]

        self.stdout.write(self.style.WARNING("DRY RUN: nothing will be changed. Add --yes to delete.\n") if not execute
                          else self.style.ERROR("DELETING demo data...\n"))
        plan = [(label, getattr(model, "_base_manager", model.objects).count()) for label, model in self.models()]
        for label, n in plan:
            self.stdout.write(f"  {label:<36} {n}")
        self.stdout.write(f"  {'workers/employees to remove':<36} {targets.count()}  (keeping {len(keep)}: superusers, role holders"
                          f"{', and --keep-badges' if options['keep_badges'] else ''})")
        for e in Employee.objects.entire().filter(pk__in=keep).select_related("employee_user_id")[:20]:
            self.stdout.write(f"      keeps: {e.badge_id or '-'} {e.get_full_name()} ({getattr(e.employee_user_id, 'username', '-')})")
        if not execute:
            self.stdout.write("\nThe setup (company, departments, roles, rules, bank list, mail server) is not touched.")
            return

        with transaction.atomic():
            for label, model in self.models():
                n = getattr(model, "_base_manager", model.objects).count()
                self.delete_all(model)
                self.stdout.write(f"  removed {n:>6}  {label}")
            removed_emp = self.delete_employees(targets, target_user_ids, user_model)
            self.stdout.write(f"  removed {removed_emp:>6}  workers/employees (and their login accounts)")
        self.stdout.write(self.style.SUCCESS("\nDone. The setup is untouched; you can now enter real data."))

    # ------------------------------------------------------------- helpers
    def delete_all(self, model):
        manager = getattr(model, "_base_manager", model.objects)
        if model.__name__ == "LoanAccount":
            for loan in manager.all():          # Horilla's own delete() also removes the schedule it generated
                loan.delete()
            manager.all().delete()
        else:
            manager.all().delete()

    def delete_employees(self, targets, user_ids, user_model):
        """Delete the workers' users (which cascades to the employee). Employee-owned rows that PROTECT them are
        removed and the delete retried; anything touching a kept employee aborts the whole run."""
        from employee.models import Employee

        count = targets.count()
        for _round in range(12):
            try:
                user_model.objects.filter(pk__in=user_ids).delete()
                Employee.objects.entire().filter(pk__in=list(targets.values_list("pk", flat=True))).delete()
                return count
            except ProtectedError as exc:
                blockers = list(exc.protected_objects)
                if any(type(b).__name__ in KEEP_BLOCK_MODELS for b in blockers):
                    raise CommandError(
                        "A worker to be removed is still referenced by another employee's record "
                        f"({blockers[0]!r}, e.g. as reporting manager). Nothing was changed. Keep that worker with --keep-badges."
                    ) from exc
                by_model = {}
                for b in blockers:
                    by_model.setdefault(type(b), []).append(b.pk)
                for model, pks in by_model.items():
                    getattr(model, "_base_manager", model.objects).filter(pk__in=pks).delete()
                    self.stdout.write(f"    also removed {len(pks)} {model._meta.label} record(s) that belonged to those workers")
        raise CommandError("Could not clear all the workers' records after several attempts. Nothing was changed.")
