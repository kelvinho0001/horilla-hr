from django.contrib import admin

from .models import AttendanceRule, AttendanceRuleWindow


class WindowInline(admin.TabularInline):
    model = AttendanceRuleWindow
    extra = 2


@admin.register(AttendanceRule)
class AttendanceRuleAdmin(admin.ModelAdmin):
    inlines = [WindowInline]
    list_display = ("name", "scope", "is_active")
