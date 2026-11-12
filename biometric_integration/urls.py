from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="bio-int-dashboard"),
    path("upload/", views.upload, name="bio-int-upload"),
    path("batches/", views.batch_list, name="bio-int-batches"),
    path("batches/<int:pk>/", views.batch_detail, name="bio-int-batch-detail"),
    path("mappings/", views.mapping_list, name="bio-int-mapping-list"),
    path("mappings/new/", views.mapping_create, name="bio-int-mapping-create"),
    path("mappings/<int:pk>/deactivate/", views.mapping_deactivate, name="bio-int-mapping-deactivate"),
    path("unmapped/", views.unmapped_list, name="bio-int-unmapped"),
    path("unmapped/<int:provider_id>/map-all/", views.map_all, name="bio-int-map-all"),
    path("unmapped/<int:provider_id>/ignore/", views.ignore_id, name="bio-int-ignore"),
    path("unmapped/<int:provider_id>/unignore/", views.unignore_id, name="bio-int-unignore"),
    path("worker-sheet/", views.worker_sheet_view, name="bio-int-worker-sheet"),
]
