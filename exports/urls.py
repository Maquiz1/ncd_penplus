from django.urls import path

from . import views

app_name = 'exports'

urlpatterns = [
    path('', views.ExportListView.as_view(), name='list'),
    path('status/', views.ExportStatusView.as_view(), name='status'),
    path('<int:pk>/download/', views.ExportDownloadView.as_view(), name='download'),
    path('<int:pk>/remove/', views.ExportDeleteView.as_view(), name='remove'),
    path('codebook/', views.CodebookView.as_view(), name='codebook'),
    path('forms/', views.FormsView.as_view(), name='forms'),
    path('forms/download/', views.FormExportView.as_view(), name='form_download'),
]
