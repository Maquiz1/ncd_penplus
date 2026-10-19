from django.shortcuts import render
from django.utils import timezone

from facilities.scope import scope_description
from reporting.services import dashboard_stats


def dashboard(request):
    context = {'today': timezone.localdate(), 'scope': scope_description(request.user)}
    if request.user.has_perm('reporting.view_reports') or request.user.has_perm('patients.view_patient'):
        context['stats'] = dashboard_stats(request.user)
    return render(request, 'core/dashboard.html', context)
