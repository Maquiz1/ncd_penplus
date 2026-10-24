from django.urls import path

from . import views

app_name = 'records'

urlpatterns = [
    path('', views.RecordsHomeView.as_view(), name='home'),
    path('<slug:key>/', views.RecordListView.as_view(), name='list'),
    path('<slug:key>/<int:pk>/edit/', views.RecordEditView.as_view(), name='edit'),
    path('<slug:key>/<int:pk>/delete/', views.RecordDeleteView.as_view(), name='delete'),
    path('<slug:key>/<int:pk>/restore/', views.RecordRestoreView.as_view(), name='restore'),
]
