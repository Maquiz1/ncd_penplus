from django.urls import path

from . import views

app_name = 'indicators'

urlpatterns = [
    path('quarterly/', views.QuarterlyView.as_view(), name='quarterly'),
    path('clinical/', views.ClinicalView.as_view(), name='clinical'),
    path('sociodemographic/', views.SociodemographicView.as_view(), name='socio'),
    path('patients/', views.DrillView.as_view(), name='drill'),
]
