from django.contrib.auth.mixins import PermissionRequiredMixin
from django.views.generic import ListView

from .models import Facility
from .scope import facilities_for_user


class FacilityListView(PermissionRequiredMixin, ListView):
    permission_required = 'facilities.view_facility'
    template_name = 'facilities/facility_list.html'
    paginate_by = 50

    def get_queryset(self):
        qs = facilities_for_user(self.request.user).select_related('district__region__zone')
        if self.request.GET.get('q'):
            qs = qs.filter(name__icontains=self.request.GET['q'])
        return qs.order_by('name')

    def get_context_data(self, **kwargs):
        return super().get_context_data(model=Facility, **kwargs)
