from django.urls import path

from . import views

app_name = 'notifications'

urlpatterns = [
    path('', views.SmsLogView.as_view(), name='log'),
    path('send-queued/', views.SmsSendQueuedView.as_view(), name='send_queued'),
    path('settings/', views.SmsSettingsView.as_view(), name='settings'),
    path('settings/test/', views.SendTestSmsView.as_view(), name='test'),
]
