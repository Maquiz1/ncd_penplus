from django.urls import path

from . import views

app_name = 'laboratory'

urlpatterns = [
    path('lab/', views.LabWorklistView.as_view(), name='lab_worklist'),
    path('patients/<int:patient_pk>/lab/add/', views.LabOrderCreateView.as_view(), name='lab_add'),
    path('lab/<int:pk>/<str:step>/', views.LabOrderActionView.as_view(), name='lab_action'),
    path('monitoring/hba1c/', views.MonitoringListView.as_view(), name='monitoring'),
    path('patients/<int:patient_pk>/hba1c-not-done/', views.MonitoringGapCreateView.as_view(), name='gap_add'),
    path('monitoring/settings/', views.MonitoringRuleListView.as_view(), name='monitoring_rules'),
    path('monitoring/settings/<int:pk>/edit/', views.MonitoringRuleUpdateView.as_view(), name='monitoring_rule_edit'),
    path('catalogue/', views.LabCatalogueView.as_view(), name='catalogue'),
    path('catalogue/tests/new/', views.LabTestCreateView.as_view(), name='test_create'),
    path('catalogue/tests/<int:pk>/edit/', views.LabTestUpdateView.as_view(), name='test_update'),
    path('catalogue/categories/new/', views.LabCategoryCreateView.as_view(), name='category_create'),
    path('catalogue/categories/<int:pk>/edit/', views.LabCategoryUpdateView.as_view(), name='category_update'),
    path('catalogue/panels/new/', views.LabPanelCreateView.as_view(), name='panel_create'),
    path('catalogue/panels/<int:pk>/edit/', views.LabPanelUpdateView.as_view(), name='panel_update'),
]
