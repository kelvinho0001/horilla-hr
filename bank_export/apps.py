from django.apps import AppConfig
from django.conf import settings
from django.db.models.signals import post_migrate


class BankExportConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "bank_export"
    verbose_name = "Bank Export"

    def ready(self):
        from auditlog.registry import auditlog
        from django.urls import include, path

        from horilla.urls import urlpatterns

        from . import models, permissions

        for m in (models.Bank, models.EmployeeBankLink, models.ExportBatch, models.PaymentTemplate):
            if not auditlog.contains(m):
                if m is models.ExportBatch:
                    auditlog.register(m, exclude_fields=["content"])
                else:
                    auditlog.register(m)
        post_migrate.connect(permissions.sync_groups, sender=self)
        settings.APPS.append("bank_export")
        urlpatterns.append(path("bank-export/", include("bank_export.urls")))
        # Another app's sidebar may already have resolved URLs (it reverses at import); rebuild the cache.
        from django.urls import clear_url_caches

        clear_url_caches()
        from . import sidebar  # noqa: F401

        super().ready()
