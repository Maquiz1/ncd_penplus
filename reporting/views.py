import csv

from django.contrib.auth.mixins import PermissionRequiredMixin
from django.http import HttpResponse
from django.utils import timezone
from django.views import View
from django.views.generic import ListView, TemplateView

from audit.models import AuditEvent
from audit.services import log_event
from core.forms import DateRangeForm
from facilities.scope import scope_description
from patients.models import Patient

from .models import ReportRun
from .services import period_report


def selected_period(request):
    today = timezone.localdate()
    form = DateRangeForm(request.GET or None)
    start, end = today.replace(day=1), today
    if form.is_valid():
        start = form.cleaned_data['start'] or start
        end = form.cleaned_data['end'] or end
    return form, start, end


class ReportView(PermissionRequiredMixin, TemplateView):
    permission_required = 'reporting.view_reports'
    template_name = 'reporting/report.html'

    def get_context_data(self, **kwargs):
        form, start, end = selected_period(self.request)
        if not form.is_bound:
            form = DateRangeForm(initial={'start': start, 'end': end})
        return super().get_context_data(form=form, report=period_report(self.request.user, start, end),
                                        scope=scope_description(self.request.user), generated=timezone.now(), **kwargs)


class PatientExportView(PermissionRequiredMixin, View):
    """Scope-limited patient register export. Identity columns only with identity permission."""

    permission_required = ('reporting.export_data', 'patients.view_patient')

    def get(self, request):
        with_identity = request.user.has_perm('patients.view_patient_identity')
        patients = Patient.objects.in_scope(request.user).select_related('current_facility', 'registration_facility')
        response = HttpResponse(content_type='text/csv')
        stamp = timezone.localtime().strftime('%Y%m%d-%H%M')
        response['Content-Disposition'] = f'attachment; filename="patient-register-{stamp}.csv"'
        writer = csv.writer(response)
        header = ['patient_id', 'sex', 'age', 'registration_date', 'registration_facility', 'current_facility',
                  'status', 'vital_status']
        if with_identity:
            header[1:1] = ['first_name', 'middle_name', 'last_name', 'date_of_birth', 'phone']
        writer.writerow(header)
        count = 0
        for p in patients.iterator():
            row = [p.patient_number, p.sex, p.age, p.registration_date, p.registration_facility.name,
                   p.current_facility.name, p.status, p.vital_status]
            if with_identity:
                row[1:1] = [p.first_name, p.middle_name, p.last_name, p.date_of_birth, p.phone]
            writer.writerow(row)
            count += 1
        log_event(request=request, action=AuditEvent.Action.EXPORT, model='patients.Patient',
                  object_repr=f'Patient register ({count} rows)',
                  changes={'identity_columns': with_identity, 'scope': scope_description(request.user)})
        return response


class ReportRunListView(PermissionRequiredMixin, ListView):
    permission_required = 'reporting.view_reportrun'
    model = ReportRun
    template_name = 'reporting/run_list.html'
    paginate_by = 50

    def get_queryset(self):
        return ReportRun.objects.select_related('recipient')
