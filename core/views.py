from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import render
from django.utils import timezone
from django.views import View

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


class SopView(LoginRequiredMixin, View):
    """The SOP for everyone who uses the system: read it here, or download the current or an issued version as PDF."""

    def get(self, request, version=None):
        from django.http import Http404, HttpResponse

        from . import sop
        if version is not None:
            path = dict(sop.issued_versions()).get(version)
            if path is None:
                raise Http404
            pdf = path.read_bytes()
        elif request.GET.get('format') == 'pdf':
            version, pdf = sop.VERSION, sop.build_pdf()
        else:
            return render(request, 'core/sop.html', {
                'body': sop.body_html(), 'version': sop.VERSION, 'version_date': sop.VERSION_DATE,
                'issued': sop.issued_versions(), 'change_log': sop.CHANGE_LOG})
        response = HttpResponse(pdf, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="{sop.pdf_filename(version)}"'
        return response
