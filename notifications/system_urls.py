from django.urls import path

from . import system_views as views

app_name = 'system'

urlpatterns = [
    path('', views.SystemSettingsView.as_view(), name='home'),
    path('jobs/', views.ScheduledJobsView.as_view(), name='jobs'),
    path('jobs/<int:pk>/<str:action>/', views.ScheduledJobsView.as_view(), name='job_action'),
]
