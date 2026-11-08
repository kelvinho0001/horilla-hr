from django.apps import AppConfig
from django.conf import settings
from django.db.models.signals import post_migrate


class WeeklyPayrollConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "weekly_payroll"
    verbose_name = "Weekly Payroll"

    def ready(self):
        from django.urls import include, path

        from horilla.urls import urlpatterns

        from . import permissions

        post_migrate.connect(permissions.sync_groups, sender=self)
        settings.APPS.append("weekly_payroll")
        urlpatterns.append(path("weekly-payroll/", include("weekly_payroll.urls")))
        from . import sidebar  # noqa: F401

        super().ready()
