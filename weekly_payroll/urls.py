from django.urls import path

from . import views as v

urlpatterns = [
    path("", v.run_list, name="wp-runs"),
    path("runs/new/", v.run_create, name="wp-run-new"),
    path("runs/<int:pk>/", v.run_detail, name="wp-run-detail"),
    path("runs/<int:pk>/recalculate/", v.run_recalculate, name="wp-run-recalculate"),
    path("runs/<int:pk>/review/", v.run_review, name="wp-run-review"),
    path("runs/<int:pk>/approve/", v.run_approve, name="wp-run-approve"),
    path("runs/<int:pk>/download/", v.run_download, name="wp-run-download"),
    path("lines/<int:pk>/working-days/", v.line_working_days, name="wp-line-days"),
]
