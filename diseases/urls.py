from django.urls import path

from . import views

app_name = 'diseases'

urlpatterns = [
    path('', views.DiseaseListView.as_view(), name='list'),
    path('new/', views.DiseaseCreateView.as_view(), name='create'),
    path('<int:pk>/', views.DiseaseDetailView.as_view(), name='detail'),
    path('<int:pk>/edit/', views.DiseaseUpdateView.as_view(), name='update'),
    path('<int:pk>/toggle/', views.DiseaseToggleView.as_view(), name='toggle'),
    path('<int:pk>/delete/', views.DiseaseDeleteView.as_view(), name='delete'),
    path('<int:pk>/restore/', views.DiseaseRestoreView.as_view(), name='restore'),
    path('<int:pk>/relink/', views.RelinkDiagnosesView.as_view(), name='relink'),
    path('<int:disease_pk>/conditions/new/', views.ConditionCreateView.as_view(), name='condition_create'),
    path('<int:disease_pk>/conditions/<int:pk>/edit/', views.ConditionUpdateView.as_view(), name='condition_update'),
]
