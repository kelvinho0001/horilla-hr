"""
Attendance is now validated on Horilla's own Attendances page, so the separate daily-attendance, approval
and correction tables are removed (they were derived from the raw punches, which are kept). Rules keep their
windows; the weekday setting is gone because a valid punch counts on any day.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [("attendance_processing", "0002_rule_windows")]

    operations = [
        migrations.DeleteModel(name="AttendanceCorrection"),
        migrations.DeleteModel(name="DailyAttendance"),
        migrations.DeleteModel(name="AttendanceApproval"),
        migrations.AlterModelOptions(
            name="attendancerule",
            options={
                "permissions": [("process_attendance", "Can add biometric punches to Horilla attendance")],
                "verbose_name": "Attendance rule",
                "verbose_name_plural": "Attendance rules",
            },
        ),
        migrations.RemoveField(model_name="attendancerule", name="working_weekdays"),
    ]
