from django.urls import path, re_path

from . import views

app_name = 'facilities'

urlpatterns = [
    path('', views.FacilityListView.as_view(), name='list'),
    path('new/', views.FacilityCreateView.as_view(), name='create'),
    path('<int:pk>/edit/', views.FacilityUpdateView.as_view(), name='update'),
    re_path(r'^locations/(?P<level>regions|districts|wards|streets)/$', views.location_options, name='location_options'),
]
