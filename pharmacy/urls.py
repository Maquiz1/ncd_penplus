from django.urls import path

from . import views

app_name = 'pharmacy'

urlpatterns = [
    path('medications/', views.MedicationListView.as_view(), name='medication_list'),
    path('medications/new/', views.MedicationCreateView.as_view(), name='medication_create'),
    path('medications/<int:pk>/edit/', views.MedicationUpdateView.as_view(), name='medication_update'),
    path('stock/', views.StockListView.as_view(), name='stock_list'),
    path('stock/receive/', views.ReceiveStockView.as_view(), name='stock_receive'),
    path('stock/<int:pk>/adjust/', views.AdjustStockView.as_view(), name='stock_adjust'),
    path('stock/ledger/', views.MovementListView.as_view(), name='movement_list'),
    path('consumables/add/', views.ConsumableCreateView.as_view(), name='consumable_add'),
    path('consumables/<int:pk>/toggle/', views.ConsumableRemoveView.as_view(), name='consumable_toggle'),
    path('stock-outs/', views.ShortfallListView.as_view(), name='shortfall_list'),
    path('stock-outs/<int:pk>/resolve/', views.ShortfallResolveView.as_view(), name='shortfall_resolve'),
    path('prescriptions/', views.PrescriptionListView.as_view(), name='prescription_list'),
    path('patients/<int:patient_pk>/prescribe/', views.PrescriptionCreateView.as_view(), name='prescription_add'),
    path('prescriptions/<int:pk>/dispense/', views.DispenseView.as_view(), name='dispense'),
    path('prescriptions/<int:pk>/cancel/', views.PrescriptionCancelView.as_view(), name='prescription_cancel'),
]
