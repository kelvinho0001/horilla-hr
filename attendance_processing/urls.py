from django.urls import path

from . import views

urlpatterns = [
    path("", views.review, name="att-proc-review"),
    path("process/", views.process, name="att-proc-process"),
    path("correct/", views.correct, name="att-proc-correct"),
    path("bulk-absent/", views.bulk_absent, name="att-proc-bulk-absent"),
    path("approve/", views.approve, name="att-proc-approve"),
    path("approvals/", views.approvals, name="att-proc-approvals"),
    path("approvals/<int:pk>/reopen/", views.reopen, name="att-proc-reopen"),
    path("day/<int:employee_id>/<str:day>/", views.day_detail, name="att-proc-day"),
    path("rules/", views.rule_list, name="att-proc-rules"),
    path("rules/new/", views.rule_form, name="att-proc-rule-new"),
    path("rules/<int:pk>/", views.rule_form, name="att-proc-rule-edit"),
]
