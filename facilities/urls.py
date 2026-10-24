from django.urls import path, re_path

from . import views

app_name = 'facilities'

urlpatterns = [
    path('', views.FacilityListView.as_view(), name='list'),
    re_path(r'^locations/(?P<level>regions|districts|wards|streets)/$', views.location_options, name='location_options'),
]
