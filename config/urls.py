"""URL configuration for the NCD Clinical and Research Management System."""
from django.contrib import admin
from django.urls import include, path

from core.views import SopView, dashboard
from users.views import AccountPasswordResetConfirmView, LoggedPasswordResetView

admin.site.site_header = 'RECAPEN administration'
admin.site.site_title = 'RECAPEN admin'

urlpatterns = [
    path('', dashboard, name='dashboard'),
    path('sop/', SopView.as_view(), name='sop'),
    path('sop/v<str:version>/', SopView.as_view(), name='sop_version'),
    path('admin/', admin.site.urls),
    path('accounts/password_reset/', LoggedPasswordResetView.as_view(), name='password_reset'),
    path('accounts/reset/<uidb64>/<token>/', AccountPasswordResetConfirmView.as_view(), name='password_reset_confirm'),
    path('accounts/', include('django.contrib.auth.urls')),
    path('staff/', include('users.urls')),
    path('facilities/', include('facilities.urls')),
    path('patients/', include('patients.urls')),
    path('clinical/', include('clinical.urls')),
    path('diseases/', include('diseases.urls')),
    path('research/', include('research.urls')),
    path('encounters/', include('encounters.urls')),
    path('crf/', include('crf.urls')),
    path('lab/', include('laboratory.urls')),
    path('investigations/', include('investigations.urls')),
    path('records/', include('records.urls')),
    path('exports/', include('exports.urls')),
    path('pharmacy/', include('pharmacy.urls')),
    path('reports/', include('reporting.urls')),
    path('indicators/', include('indicators.urls')),
    path('audit/', include('audit.urls')),
    path('issues/', include('issues.urls')),
    path('sms/', include('notifications.urls')),
    path('email/', include('notifications.email_urls')),
    path('system/', include('notifications.system_urls')),
]
