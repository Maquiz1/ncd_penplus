from django.urls import path

from . import views

app_name = 'encounters'

urlpatterns = [
    path('appointments/', views.AppointmentListView.as_view(), name='appointment_list'),
    path('patients/<int:patient_pk>/appointments/add/', views.AppointmentCreateView.as_view(), name='appointment_add'),
    path('appointments/<int:pk>/edit/', views.AppointmentUpdateView.as_view(), name='appointment_update'),
    path('appointments/<int:pk>/status/', views.AppointmentStatusView.as_view(), name='appointment_status'),
    path('', views.EncounterListView.as_view(), name='encounter_list'),
    path('patients/<int:patient_pk>/encounters/add/', views.EncounterCreateView.as_view(), name='encounter_add'),
    path('<int:pk>/', views.EncounterDetailView.as_view(), name='encounter_detail'),
    path('<int:pk>/edit/', views.EncounterUpdateView.as_view(), name='encounter_update'),
    path('<int:encounter_pk>/vitals/add/', views.VitalsCreateView.as_view(), name='vitals_add'),
    path('patients/<int:patient_pk>/schedule/add/', views.FollowUpPlanCreateView.as_view(), name='plan_add'),
    path('schedule/<int:pk>/edit/', views.FollowUpPlanUpdateView.as_view(), name='plan_update'),
    path('schedule/<int:pk>/generate/', views.FollowUpPlanGenerateView.as_view(), name='plan_generate'),
    path('admissions/', views.AdmissionListView.as_view(), name='admission_list'),
    path('patients/<int:patient_pk>/admissions/add/', views.AdmissionCreateView.as_view(), name='admission_add'),
    path('admissions/<int:pk>/discharge/', views.AdmissionDischargeView.as_view(), name='admission_discharge'),
]
