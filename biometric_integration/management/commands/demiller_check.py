"""
Pre-flight and post-deploy check for the Demiller Farms customisation.

    python manage.py demiller_check

Exit code 1 if any FAIL. WARN items should be read, not ignored.
"""

import sys
from decimal import Decimal

from django.apps import apps
from django.conf import settings
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

CUSTOM_APPS = ["biometric_integration", "attendance_processing", "weekly_payroll", "bank_export"]


class Command(BaseCommand):
    help = "Check that this installation is configured safely for Demiller Farms."

    def handle(self, *args, **options):
        results = []

        def add(level, message):
            results.append((level, message))

        for app in CUSTOM_APPS:
            add("OK" if apps.is_installed(app) else "FAIL", f"app installed: {app}")

        if settings.DEBUG:
            add("FAIL", "DEBUG is on. Set DEBUG=False in production.")
        else:
            add("OK", "DEBUG is off")
        if settings.SECRET_KEY.startswith(("django-insecure", "change-me")):
            add("FAIL", "SECRET_KEY is a placeholder")
        else:
            add("OK", "SECRET_KEY is set")
        if "*" in settings.ALLOWED_HOSTS or not settings.ALLOWED_HOSTS:
            add("FAIL", "ALLOWED_HOSTS must list the real hostname(s), not *")
        else:
            add("OK", f"ALLOWED_HOSTS = {', '.join(settings.ALLOWED_HOSTS)}")
        if not any(o.startswith("https://") for o in settings.CSRF_TRUSTED_ORIGINS):
            add("WARN", "CSRF_TRUSTED_ORIGINS has no https:// origin")
        if not getattr(settings, "SECURE_SSL_REDIRECT", False):
            add("WARN", "SECURE_SSL_REDIRECT is off; enable it once HTTPS works")

        engine = connection.vendor
        if engine == "postgresql":
            add("OK", "database engine: postgresql")
        elif engine == "sqlite":
            from pathlib import Path

            name = str(connection.settings_dict["NAME"])
            add("WARN", "database engine: SQLite. Fine for a small team (a few HR users at a time); "
                "back it up with scripts/backup.sh (SQLITE_PATH) and move to PostgreSQL 14+ if usage grows")
            if str(settings.BASE_DIR) in str(Path(name).resolve()):
                add("WARN", "the SQLite file is inside the code folder; keep it elsewhere (DB_NAME=/home/<user>/hrm_data/horilla.sqlite3)")
        else:
            add("WARN", f"database engine: {engine} (not supported by this setup)")

        tz = getattr(settings, "ATTENDANCE_TIMEZONE", "Africa/Lagos")
        add("OK", f"attendance day uses {tz}")
        if settings.TIME_ZONE != tz:
            add("WARN", f"TIME_ZONE is {settings.TIME_ZONE}; set TIME_ZONE={tz} so screens show local time")

        try:
            executor = MigrationExecutor(connection)
            pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
            add("FAIL" if pending else "OK", f"{len(pending)} unapplied migration(s)" if pending else "all migrations applied")

            from attendance_processing.models import AttendanceRule
            from bank_export.models import Bank, PaymentTemplate
            from weekly_payroll.models import PayrollRun

            for name in ("HR Officer", "HR/Payroll Approver", "Management", "Accountant", "IT Administrator"):
                add("OK" if Group.objects.filter(name=name).exists() else "FAIL", f"role group exists: {name}")
            add("OK" if Bank.objects.exists() else "WARN", f"{Bank.objects.count()} banks loaded"
                + ("" if Bank.objects.exists() else " (run load_banks)"))
            add("OK" if PaymentTemplate.objects.filter(is_default=True).exists() else "FAIL", "default payment template present")
            add("OK" if AttendanceRule.objects.filter(is_active=True).exists() else "WARN",
                "attendance rules configured" if AttendanceRule.objects.filter(is_active=True).exists()
                else "no attendance rule: any punch counts, Mon-Sat working days (the built-in fallback)")
            add("OK", f"{PayrollRun.objects.count()} payroll run(s) on record")
        except Exception as exc:  # database unreachable etc.
            add("FAIL", f"database check failed: {exc}")

        for level, message in results:
            self.stdout.write(f"[{level:<4}] {message}")
        fails = sum(1 for level, _m in results if level == "FAIL")
        warns = sum(1 for level, _m in results if level == "WARN")
        self.stdout.write(f"\n{fails} failure(s), {warns} warning(s)")
        if fails:
            sys.exit(1)
