from django.urls import path

from . import views

app_name = 'clinical'

urlpatterns = [
    path('screenings/', views.ScreeningListView.as_view(), name='screening_list'),
    path('patients/<int:patient_pk>/family/add/', views.FamilyMemberCreateView.as_view(), name='family_add'),
    path('family/<int:pk>/edit/', views.FamilyMemberUpdateView.as_view(), name='family_edit'),
    path('sessions/', views.ScreeningSessionListView.as_view(), name='session_list'),
    path('sessions/new/', views.ScreeningSessionCreateView.as_view(), name='session_create'),
    path('sessions/<int:pk>/', views.ScreeningSessionDetailView.as_view(), name='session_detail'),
    path('sessions/<int:pk>/edit/', views.ScreeningSessionUpdateView.as_view(), name='session_update'),
    path('sessions/<int:pk>/screen/', views.SessionScreenView.as_view(), name='session_screen'),
    path('flow-gaps/', views.FlowGapsView.as_view(), name='flow_gaps'),
    path('enrollments/', views.ProgrammeEnrollmentListView.as_view(), name='enrollment_list'),
    path('patients/<int:patient_pk>/prescreening/add/', views.PreScreeningCreateView.as_view(), name='prescreening_add'),
    path('prescreening/<int:pk>/edit/', views.PreScreeningUpdateView.as_view(), name='prescreening_update'),
    path('prescreening/<int:pk>/delete/', views.PreScreeningDeleteView.as_view(), name='prescreening_delete'),
    path('patients/<int:patient_pk>/screening/add/', views.ScreeningCreateView.as_view(), name='screening_add'),
    path('screening/<int:pk>/edit/', views.ScreeningUpdateView.as_view(), name='screening_update'),
    path('patients/<int:patient_pk>/diagnosis/add/', views.DiagnosisCreateView.as_view(), name='diagnosis_add'),
    path('diagnosis/<int:pk>/edit/', views.DiagnosisUpdateView.as_view(), name='diagnosis_update'),
    path('patients/<int:patient_pk>/enrollment/add/', views.ProgrammeEnrollmentCreateView.as_view(), name='enrollment_add'),
    path('enrollment/<int:pk>/edit/', views.ProgrammeEnrollmentUpdateView.as_view(), name='enrollment_update'),
    path('enrollment/<int:pk>/return/', views.ReturnToCareView.as_view(), name='enrollment_return'),
]
