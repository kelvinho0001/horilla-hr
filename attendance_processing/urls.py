from django.urls import path

from . import views

urlpatterns = [
    path("", views.review, name="att-proc-review"),
    path("process/", views.process, name="att-proc-process"),
    path("accept/", views.accept, name="att-proc-accept"),
    path("day/<int:employee_id>/<str:day>/", views.day_detail, name="att-proc-day"),
    path("rules/", views.rule_list, name="att-proc-rules"),
    path("rules/new/", views.rule_form, name="att-proc-rule-new"),
    path("rules/<int:pk>/", views.rule_form, name="att-proc-rule-edit"),
]
