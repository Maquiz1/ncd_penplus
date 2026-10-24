from django.urls import path

from . import views

app_name = 'patients'

urlpatterns = [
    path('', views.PatientListView.as_view(), name='list'),
    path('register/', views.PatientCreateView.as_view(), name='create'),
    path('deleted/', views.DeletedPatientListView.as_view(), name='deleted'),
    path('duplicates/', views.PossibleDuplicatesView.as_view(), name='duplicates'),
    path('transfers/', views.TransferListView.as_view(), name='transfers'),
    path('transfers/<int:pk>/<str:action>/', views.TransferActionView.as_view(), name='transfer_action'),
    path('<int:pk>/', views.PatientDetailView.as_view(), name='detail'),
    path('<int:pk>/edit/', views.PatientUpdateView.as_view(), name='update'),
    path('<int:pk>/delete/', views.PatientSoftDeleteView.as_view(), name='delete'),
    path('<int:pk>/restore/', views.PatientRestoreView.as_view(), name='restore'),
    path('<int:pk>/death/', views.PatientRecordDeathView.as_view(), name='record_death'),
    path('<int:pk>/sms-consent/', views.SmsConsentView.as_view(), name='sms_consent'),
    path('<int:patient_pk>/identifiers/add/', views.IdentifierCreateView.as_view(), name='identifier_add'),
    path('<int:patient_pk>/transfer/', views.TransferCreateView.as_view(), name='transfer_add'),
]
