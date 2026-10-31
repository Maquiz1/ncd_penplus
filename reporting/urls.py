from django.urls import path

from . import views

app_name = 'reporting'

urlpatterns = [
    path('', views.ReportsHomeView.as_view(), name='home'),
    path('summary/', views.ReportView.as_view(), name='report'),
    path('recruitment/', views.RecruitmentReportView.as_view(), name='recruitment'),
    path('disease/', views.DiseaseReportView.as_view(), name='disease'),
    path('conditions/', views.ConditionReportView.as_view(), name='conditions'),
    path('trends/', views.TrendsReportView.as_view(), name='trends'),
    path('study-progress/', views.StudyProgressView.as_view(), name='study_progress'),
    path('follow-up/', views.FollowUpReportView.as_view(), name='follow_up'),
    path('data-quality/', views.DataQualityReportView.as_view(), name='data_quality'),
    path('export/patients.csv', views.PatientExportView.as_view(), name='export_patients'),
    path('runs/', views.ReportRunListView.as_view(), name='runs'),
]
