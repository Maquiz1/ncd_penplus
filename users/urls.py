from django.urls import path

from . import views

app_name = 'users'

urlpatterns = [
    path('', views.StaffListView.as_view(), name='list'),
    path('new/', views.StaffCreateView.as_view(), name='create'),
    path('send-activation/', views.StaffBulkActivationView.as_view(), name='bulk_activation'),
    path('<int:pk>/', views.StaffDetailView.as_view(), name='detail'),
    path('<int:pk>/edit/', views.StaffUpdateView.as_view(), name='update'),
    path('<int:pk>/toggle-active/', views.StaffToggleActiveView.as_view(), name='toggle_active'),
    path('<int:pk>/resend-activation/', views.StaffResendActivationView.as_view(), name='resend_activation'),
    path('<int:pk>/assignments/add/', views.AssignmentCreateView.as_view(), name='assignment_add'),
    path('<int:pk>/assignments/<int:assignment_pk>/end/', views.AssignmentEndView.as_view(), name='assignment_end'),
]
