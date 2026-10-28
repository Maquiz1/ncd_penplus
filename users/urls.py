from django.urls import path

from . import views

app_name = 'users'

urlpatterns = [
    path('', views.StaffListView.as_view(), name='list'),
    path('new/', views.StaffCreateView.as_view(), name='create'),
    path('roles/', views.RoleMatrixView.as_view(), name='roles'),
    path('positions/', views.PositionListView.as_view(), name='positions'),
    path('positions/add/', views.PositionCreateView.as_view(), name='position_add'),
    path('positions/<int:pk>/edit/', views.PositionUpdateView.as_view(), name='position_edit'),
    path('<int:pk>/identity-access/', views.IdentityAccessView.as_view(), name='identity_access'),
    path('send-activation/', views.StaffBulkActivationView.as_view(), name='bulk_activation'),
    path('<int:pk>/', views.StaffDetailView.as_view(), name='detail'),
    path('<int:pk>/edit/', views.StaffUpdateView.as_view(), name='update'),
    path('<int:pk>/toggle-active/', views.StaffToggleActiveView.as_view(), name='toggle_active'),
    path('<int:pk>/resend-activation/', views.StaffResendActivationView.as_view(), name='resend_activation'),
    path('<int:pk>/set-password/', views.StaffSetPasswordView.as_view(), name='set_password'),
    path('<int:pk>/delete/', views.StaffDeleteView.as_view(), name='delete'),
    path('<int:pk>/restore/', views.StaffRestoreView.as_view(), name='restore'),
    path('<int:pk>/assignments/add/', views.AssignmentCreateView.as_view(), name='assignment_add'),
    path('<int:pk>/assignments/<int:assignment_pk>/end/', views.AssignmentEndView.as_view(), name='assignment_end'),
]
