"""
Roles, the Horilla way: Django Groups carrying Django permissions, seeded
after migrate (compare base.signals._sync_default_hrms_groups) and checked with
``has_perm`` / ``permission_required`` in views and sidebar accessibility.
Sync is additive: it never strips permissions an administrator customised.
"""

from django.contrib.auth.models import Group, Permission

ROLE_PERMISSIONS = {
    "HR Officer": [
        "attendance_processing.view_dailyattendance",
        "attendance_processing.process_attendance",
        "attendance_processing.correct_attendance",
        "attendance_processing.view_attendanceapproval",
        "attendance_processing.view_attendancerule",
        "biometric_integration.view_importbatch",
        "biometric_integration.add_importbatch",
        "biometric_integration.view_externalemployeemapping",
        "biometric_integration.add_externalemployeemapping",
        "biometric_integration.change_externalemployeemapping",
    ],
    "HR/Payroll Approver": [
        "attendance_processing.view_dailyattendance",
        "attendance_processing.process_attendance",
        "attendance_processing.correct_attendance",
        "attendance_processing.view_attendanceapproval",
        "attendance_processing.approve_attendance",
        "attendance_processing.reopen_attendance",
        "attendance_processing.view_attendancerule",
        "attendance_processing.add_attendancerule",
        "attendance_processing.change_attendancerule",
        "biometric_integration.view_importbatch",
    ],
    "Accountant": [
        "attendance_processing.view_dailyattendance",
        "attendance_processing.view_attendanceapproval",
    ],
    "IT Administrator": [
        "biometric_integration.view_importbatch",
        "biometric_integration.add_importbatch",
        "biometric_integration.view_providerconfig",
        "biometric_integration.add_providerconfig",
        "biometric_integration.change_providerconfig",
        "biometric_integration.view_externalemployeemapping",
        "biometric_integration.add_externalemployeemapping",
        "biometric_integration.change_externalemployeemapping",
    ],
}


def sync_groups(sender=None, **kwargs):
    for name, dotted in ROLE_PERMISSIONS.items():
        group, _created = Group.objects.get_or_create(name=name)
        for entry in dotted:
            app_label, codename = entry.split(".", 1)
            perm = Permission.objects.filter(
                content_type__app_label=app_label, codename=codename
            ).first()
            if perm:  # skipped until that app's permissions exist
                group.permissions.add(perm)
