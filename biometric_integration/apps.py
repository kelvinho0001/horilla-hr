"""App config for the vendor-neutral biometric integration framework."""

from django.apps import AppConfig
from django.conf import settings


class BiometricIntegrationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "biometric_integration"
    verbose_name = "Biometric Integration"

    def ready(self):
        from auditlog.registry import auditlog
        from django.urls import include, path

        from horilla.urls import urlpatterns

        from . import models

        if not auditlog.contains(models.ExternalEmployeeMapping):
            auditlog.register(models.ExternalEmployeeMapping, serialize_data=True)
        if not auditlog.contains(models.ProviderConfig):
            auditlog.register(models.ProviderConfig, serialize_data=True)

        settings.APPS.append("biometric_integration")
        urlpatterns.append(
            path("biometric-integration/", include("biometric_integration.urls")),
        )

        from . import sidebar  # noqa: F401

        super().ready()
