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


# ---------------------------------------------------------------- address pickers (JSON for Tom Select)
from django.http import JsonResponse  # noqa: E402
from django.views.decorators.http import require_GET  # noqa: E402

from .models import District, Region, Street, Ward  # noqa: E402

LOCATION_LEVELS = {
    'regions': (Region, None),
    'districts': (District, 'region'),
    'wards': (Ward, 'district'),
    'streets': (Street, 'ward'),
}


@require_GET
def location_options(request, level):
    """Children of the chosen parent, e.g. /facilities/locations/wards/?parent=12&q=kon (login required)."""
    model, parent_field = LOCATION_LEVELS[level]
    qs = model.objects.all()
    if parent_field:
        parent = request.GET.get('parent')
        if not (parent and parent.isdigit()):
            return JsonResponse({'results': []})
        qs = qs.filter(**{f'{parent_field}_id': int(parent)})
    if request.GET.get('q'):
        qs = qs.filter(name__icontains=request.GET['q'].strip())
    return JsonResponse({'results': [{'id': pk, 'text': name} for pk, name in qs.order_by('name').values_list('pk', 'name')[:1000]]})
