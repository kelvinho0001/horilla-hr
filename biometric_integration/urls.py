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
]
