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
        # Another app's sidebar may already have resolved URLs (it reverses at import); rebuild the cache.
        from django.urls import clear_url_caches

        clear_url_caches()
        from . import sidebar  # noqa: F401

        super().ready()
