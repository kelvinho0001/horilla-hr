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
    path("profiles/", v.profile_list, name="wp-profiles"),
    path("profiles/new/", v.profile_create, name="wp-profile-new"),
    path("profiles/<int:pk>/<str:decision>/", v.profile_decide, name="wp-profile-decide"),
    path("fines/", v.fine_list, name="wp-fines"),
    path("fines/new/", v.fine_create, name="wp-fine-new"),
    path("fines/types/new/", v.fine_type_create, name="wp-fine-type-new"),
    path("fines/<int:pk>/approve/", v.fine_approve, name="wp-fine-approve"),
    path("fines/<int:pk>/waive/", v.fine_waive, name="wp-fine-waive"),
    path("loans/", v.loan_list, name="wp-loans"),
    path("loans/new/", v.loan_create, name="wp-loan-new"),
    path("loans/<int:pk>/approve/", v.loan_approve, name="wp-loan-approve"),
    path("loans/<int:pk>/adjust/", v.loan_adjust, name="wp-loan-adjust"),
]
