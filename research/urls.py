from django.urls import path

from . import views

app_name = 'research'

urlpatterns = [
    path('studies/', views.StudyListView.as_view(), name='study_list'),
    path('studies/new/', views.StudyCreateView.as_view(), name='study_create'),
    path('studies/<int:pk>/', views.StudyDetailView.as_view(), name='study_detail'),
    path('studies/<int:pk>/edit/', views.StudyUpdateView.as_view(), name='study_update'),
    path('patients/<int:patient_pk>/eligibility/add/', views.EligibilityCreateView.as_view(), name='eligibility_add'),
    path('patients/<int:patient_pk>/consent/add/', views.ConsentCreateView.as_view(), name='consent_add'),
    path('consent/<int:pk>/edit/', views.ConsentUpdateView.as_view(), name='consent_update'),
    path('patients/<int:patient_pk>/enroll/', views.StudyEnrollmentCreateView.as_view(), name='enrollment_add'),
    path('enrollment/<int:pk>/edit/', views.StudyEnrollmentUpdateView.as_view(), name='enrollment_update'),
]
