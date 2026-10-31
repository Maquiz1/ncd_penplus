from django.urls import path

from . import views

app_name = 'investigations'

urlpatterns = [
    path('', views.InvestigationWorklistView.as_view(), name='worklist'),
    path('patients/<int:patient_pk>/request/', views.InvestigationRequestView.as_view(), name='request'),
    path('<int:pk>/', views.InvestigationDetailView.as_view(), name='detail'),
    path('<int:pk>/report/', views.InvestigationReportView.as_view(), name='report'),
    path('<int:pk>/review/', views.InvestigationReviewView.as_view(), name='review'),
    path('<int:pk>/attachment/', views.InvestigationAttachmentView.as_view(), name='attachment'),
    path('catalogue/', views.CatalogueView.as_view(), name='catalogue'),
    path('catalogue/new/', views.TypeCreateView.as_view(), name='type_create'),
    path('catalogue/<int:pk>/', views.TypeDetailView.as_view(), name='type_detail'),
    path('catalogue/<int:pk>/edit/', views.TypeUpdateView.as_view(), name='type_update'),
    path('catalogue/<int:type_pk>/fields/new/', views.FieldCreateView.as_view(), name='field_create'),
    path('catalogue/fields/<int:pk>/edit/', views.FieldUpdateView.as_view(), name='field_update'),
]
