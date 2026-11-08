from django.contrib import admin

from .models import AttendanceApproval, AttendanceCorrection, AttendanceRule, DailyAttendance

admin.site.register(AttendanceRule)


@admin.register(DailyAttendance)
class DailyAdmin(admin.ModelAdmin):
    list_display = ("employee", "date", "status", "raw_count", "source", "approval")
    list_filter = ("status",)
    readonly_fields = [f.name for f in DailyAttendance._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(AttendanceApproval)
admin.site.register(AttendanceCorrection)
