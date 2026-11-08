from django.apps import AppConfig
from django.conf import settings
from django.db.models.signals import post_migrate


class AttendanceProcessingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "attendance_processing"
    verbose_name = "Attendance Processing"

    def ready(self):
        from django.urls import include, path

        from horilla.urls import urlpatterns

        from . import permissions

        post_migrate.connect(permissions.sync_groups, sender=self)
        settings.APPS.append("attendance_processing")
        urlpatterns.append(
            path("attendance-processing/", include("attendance_processing.urls")),
        )
        from . import sidebar  # noqa: F401

        super().ready()
