from django.contrib import admin

from .models import ExternalEmployeeMapping, ImportBatch, ProviderConfig, RawTransaction


@admin.register(ProviderConfig)
class ProviderConfigAdmin(admin.ModelAdmin):
    list_display = ("name", "provider_type", "source_timezone", "is_active")


@admin.register(ExternalEmployeeMapping)
class MappingAdmin(admin.ModelAdmin):
    list_display = ("provider", "external_employee_id", "employee", "status", "is_active")
    search_fields = ("external_employee_id",)


@admin.register(ImportBatch)
class BatchAdmin(admin.ModelAdmin):
    list_display = ("id", "provider", "status", "started_at", "imported_count", "duplicate_count")


@admin.register(RawTransaction)
class RawTransactionAdmin(admin.ModelAdmin):
    list_display = ("external_employee_id", "attendance_timestamp", "provider", "processing_status")
    readonly_fields = [f.name for f in RawTransaction._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
