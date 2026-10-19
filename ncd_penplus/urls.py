"""URL configuration for the NCD Clinical and Research Management System."""
from django.contrib import admin
from django.urls import include, path

from core.views import dashboard

admin.site.site_header = 'NCD System administration'
admin.site.site_title = 'NCD System admin'

urlpatterns = [
    path('', dashboard, name='dashboard'),
    path('admin/', admin.site.urls),
    path('accounts/', include('django.contrib.auth.urls')),
    path('staff/', include('users.urls')),
    path('facilities/', include('facilities.urls')),
    path('patients/', include('patients.urls')),
    path('clinical/', include('clinical.urls')),
    path('research/', include('research.urls')),
    path('encounters/', include('encounters.urls')),
    path('crf/', include('crf.urls')),
    path('lab/', include('laboratory.urls')),
    path('pharmacy/', include('pharmacy.urls')),
    path('reports/', include('reporting.urls')),
    path('audit/', include('audit.urls')),
]
