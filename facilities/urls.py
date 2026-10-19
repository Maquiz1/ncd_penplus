from django.urls import path

from . import views

app_name = 'facilities'

urlpatterns = [
    path('', views.FacilityListView.as_view(), name='list'),
]
