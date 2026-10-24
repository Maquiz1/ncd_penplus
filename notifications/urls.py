from django.urls import path

from . import views

app_name = 'notifications'

urlpatterns = [
    path('', views.SmsLogView.as_view(), name='log'),
    path('send-queued/', views.SmsSendQueuedView.as_view(), name='send_queued'),
]
