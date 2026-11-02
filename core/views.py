from django.shortcuts import render
from django.utils import timezone

from facilities.scope import scope_description
from reporting.services import dashboard_stats


def dashboard(request):
    context = {'today': timezone.localdate(), 'scope': scope_description(request.user)}
    if request.user.has_perm('reporting.view_reports') or request.user.has_perm('patients.view_patient'):
        context['stats'] = dashboard_stats(request.user)
    # Those who only read results get their own home page, without the clinic work queues.
    if request.user.has_perm('reporting.view_reports') and not request.user.has_perm('patients.view_patient'):
        return render(request, 'core/results_home.html', context)
    return render(request, 'core/dashboard.html', context)
