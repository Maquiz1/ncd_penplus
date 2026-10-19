from django.urls import path

from . import views

app_name = 'reporting'

urlpatterns = [
    path('', views.ReportView.as_view(), name='report'),
    path('export/patients.csv', views.PatientExportView.as_view(), name='export_patients'),
    path('runs/', views.ReportRunListView.as_view(), name='runs'),
]
