from django.urls import path

from . import views

app_name = 'issues'

urlpatterns = [
    path('', views.IssueListView.as_view(), name='list'),
    path('<int:pk>/', views.IssueDetailView.as_view(), name='detail'),
    path('<int:pk>/resolve/', views.IssueResolveView.as_view(), name='resolve'),
    path('<int:pk>/visits/<int:encounter_pk>/classify/', views.VisitClassifyView.as_view(), name='classify_visit'),
    path('<int:pk>/merge-location/', views.LocationMergeView.as_view(), name='merge_location'),
]
