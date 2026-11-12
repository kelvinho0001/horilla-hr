"""
Roles, the Horilla way: Django Groups carrying Django permissions, seeded after migrate
(compare base.signals._sync_default_hrms_groups) and checked with ``permission_required`` in views and
sidebar accessibility. Sync is additive: it never strips permissions an administrator customised.

Validating attendance happens on Horilla's own Attendances page, so the attendance roles also get
Horilla's attendance permissions.
"""

from django.contrib.auth.models import Group, Permission

HORILLA_ATTENDANCE = [
    "attendance.view_attendance",
    "attendance.add_attendance",
    "attendance.change_attendance",
]
ROLE_PERMISSIONS = {
    "HR Officer": [
        "attendance_processing.process_attendance",
        "attendance_processing.view_attendancerule",
        "biometric_integration.view_importbatch",
        "biometric_integration.add_importbatch",
        "biometric_integration.view_externalemployeemapping",
        "biometric_integration.add_externalemployeemapping",
        "biometric_integration.change_externalemployeemapping",
        *HORILLA_ATTENDANCE,
    ],
    "HR/Payroll Approver": [
        "attendance_processing.process_attendance",
        "attendance_processing.view_attendancerule",
        "attendance_processing.add_attendancerule",
        "attendance_processing.change_attendancerule",
        "biometric_integration.view_importbatch",
        *HORILLA_ATTENDANCE,
    ],
    "Management": ["attendance.view_attendance"],
    "Accountant": ["attendance.view_attendance"],
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
            perm = Permission.objects.filter(content_type__app_label=app_label, codename=codename).first()
            if perm:  # skipped until that app's permissions exist
                group.permissions.add(perm)
