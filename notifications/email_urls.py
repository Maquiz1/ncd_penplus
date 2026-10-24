from django.urls import path

from . import email_views as views

app_name = 'email'

urlpatterns = [
    path('', views.EmailLogView.as_view(), name='log'),
    path('settings/', views.SiteConfigurationListView.as_view(), name='settings'),
    path('settings/add/', views.SiteConfigurationCreateView.as_view(), name='settings_add'),
    path('settings/<int:pk>/edit/', views.SiteConfigurationUpdateView.as_view(), name='settings_edit'),
    path('settings/<int:pk>/activate/', views.SiteConfigurationActivateView.as_view(), name='settings_activate'),
    path('settings/test/', views.SendTestEmailView.as_view(), name='test'),
]
