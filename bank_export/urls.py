from django.urls import path

from . import views as v

urlpatterns = [
    path("", v.dashboard, name="bx-dashboard"),
    path("runs/<int:pk>/", v.run_export, name="bx-run"),
    path("runs/<int:pk>/generate/", v.generate, name="bx-generate"),
    path("batches/<int:pk>/download/", v.download, name="bx-download"),
    path("batches/<int:pk>/supersede/", v.supersede, name="bx-supersede"),
    path("batches/<int:pk>/paid/", v.paid, name="bx-paid"),
    path("banks/", v.banks, name="bx-banks"),
    path("accounts/", v.accounts, name="bx-accounts"),
    path("accounts/<int:employee_id>/", v.account_edit, name="bx-account-edit"),
]
