import django.db.models.deletion
from django.db import migrations, models


def copy_old_windows(apps, schema_editor):
    """A rule's single window becomes its first window row (nothing configured is lost)."""
    Rule = apps.get_model("attendance_processing", "AttendanceRule")
    Window = apps.get_model("attendance_processing", "AttendanceRuleWindow")
    for rule in Rule.objects.exclude(window_start__isnull=True).exclude(window_end__isnull=True):
        Window.objects.create(rule=rule, start=rule.window_start, end=rule.window_end)


def restore_first_window(apps, schema_editor):
    Rule = apps.get_model("attendance_processing", "AttendanceRule")
    for rule in Rule.objects.all():
        first = rule.windows.order_by("start").first()
        if first:
            rule.window_start, rule.window_end = first.start, first.end
            rule.save()


class Migration(migrations.Migration):

    dependencies = [("attendance_processing", "0001_initial")]

    operations = [
        migrations.CreateModel(
            name="AttendanceRuleWindow",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("start", models.TimeField()),
                ("end", models.TimeField()),
                ("rule", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE, related_name="windows",
                    to="attendance_processing.attendancerule")),
            ],
            options={"ordering": ["start", "id"]},
        ),
        migrations.RunPython(copy_old_windows, restore_first_window),
        migrations.RemoveField(model_name="attendancerule", name="window_end"),
        migrations.RemoveField(model_name="attendancerule", name="window_start"),
    ]
