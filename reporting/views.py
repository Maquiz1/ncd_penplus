import csv
from collections import Counter, OrderedDict

from django.conf import settings

from django.contrib.auth.mixins import PermissionRequiredMixin
from django.db.models import Count, DateField
from django.db.models.functions import TruncMonth
from django.http import HttpResponse
from django.utils import timezone
from django.views import View
from django.views.generic import ListView, TemplateView

from audit.models import AuditEvent
from audit.services import log_event
from core.utils import add_months
from facilities.scope import scope_description
from patients.models import Patient

from .models import ReportRun
from .pdf import FilteredReportMixin
from .reports import condition_report, recruitment_by_site, trends_report
from .services import period_report


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




class ReportsHomeView(PermissionRequiredMixin, TemplateView):
    permission_required = 'reporting.view_reports'
    template_name = 'reporting/home.html'


def month_range(start, end):
    months, month = [], start.replace(day=1)
    while month <= end:
        months.append(month)
        month = add_months(month, 1)
    return months


class ReportPage(PermissionRequiredMixin, FilteredReportMixin, TemplateView):
    permission_required = 'reporting.view_reports'
    template_name = 'reporting/filtered_page.html'

    def get_context_data(self, **kwargs):
        return super().get_context_data(scope=scope_description(self.request.user), **kwargs)


class ReportView(ReportPage):
    report_title = 'Period summary'
    body_template = 'reporting/body/summary.html'
    default_period = 'this_month'
    with_disease = False

    def get_context_data(self, **kwargs):
        f = self.get_filters()
        return super().get_context_data(report=period_report(self.request.user, f.start, f.end, f.facilities), **kwargs)


class RecruitmentReportView(ReportPage):
    report_title = 'Recruitment status by site'
    body_template = 'reporting/body/recruitment.html'

    def get_context_data(self, **kwargs):
        return super().get_context_data(data=recruitment_by_site(self.get_filters()), **kwargs)

    def csv_rows(self, context):
        data = context['data']
        yield (['Site', 'Registered', 'Screened', 'Screen positive', 'Eligible (study)', 'Enrolled']
               + [d.name for d in data['diseases']] + data['ended_labels'] + ['Active now'])
        for r in data['rows'] + [dict(data['total'], facility='Total')]:
            yield ([str(r['facility']), r['registered'], r['screened'], r['positive'], r['eligible'], r['enrolled']]
                   + r['by_disease'] + r['ended'] + [r['active']])


class ConditionReportView(ReportPage):
    report_title = 'Patients by condition'
    body_template = 'reporting/body/conditions.html'
    default_period = 'all'

    def disease(self):
        f = self.get_filters()
        return f.disease or f.diseases.filter(code='cardiac').first() or f.diseases.first()

    def get_context_data(self, **kwargs):
        disease = self.disease()
        data = condition_report(self.get_filters(), disease, self.request.GET.get('condition')) if disease else None
        return super().get_context_data(disease=disease, data=data, hidden_filters={}, **kwargs)

    def csv_rows(self, context):
        data = context['data']
        if data is None:
            return
        yield (['Condition', 'Patients', 'Active', 'New in period'] + [s.name for s in data['sites']]
               + ['Male', 'Female'] + data['age_labels'])
        for g in data['rows']:
            yield ([g['label'], g['total'], g['active'], g['new']] + g['site_counts']
                   + [g['sex'].get('male', 0), g['sex'].get('female', 0)] + g['age_counts'])


class TrendsReportView(ReportPage):
    report_title = 'Vital signs & HbA1c trends'
    body_template = 'reporting/body/trends.html'
    default_period = 'last_12'

    def get_context_data(self, **kwargs):
        return super().get_context_data(data=trends_report(self.get_filters()), **kwargs)

    def csv_rows(self, context):
        yield ['Month', 'Vitals recorded', 'Patients with BP', 'Mean SBP', 'Mean DBP', 'BP <140/90 (%)',
               'Patients with HbA1c', 'Mean HbA1c', 'HbA1c <8 (%)', 'Glucose tests', 'Mean glucose']
        for r in context['data']['rows']:
            yield [f'{r["month"]:%Y-%m}', r['vitals'], r['bp_patients'], r['sbp'], r['dbp'], r['bp_controlled'],
                   r['a1c_patients'], r['a1c_mean'], r['a1c_controlled'], r['glucose_tests'], r['glucose_mean']]


class StudyProgressView(ReportPage):
    """Research study progress: eligibility, consent, enrollment, retention and outcomes by site and month."""

    report_title = 'Study progress'
    body_template = 'reporting/body/study_progress.html'
    default_period = 'all'
    with_disease = False

    def get_context_data(self, **kwargs):
        from research.models import ConsentRecord, Study, StudyEligibilityAssessment, StudyEnrollment

        f = self.get_filters()
        studies = Study.objects.all()
        study = studies.filter(pk=self.request.GET.get('study')).first() or studies.filter(code='penplus').first() or studies.first()
        ctx = super().get_context_data(studies=studies, study=study,
                                       hidden_filters={'study': study.pk} if study else {}, **kwargs)
        if study is None:
            return ctx
        enrollments = study.enrollments.filter(facility__in=f.facilities, enrollment_date__lte=f.end)
        sites = []
        for facility in f.facilities.filter(pk__in=enrollments.values('facility')).order_by('name'):
            counts = dict(enrollments.filter(facility=facility).values_list('status').annotate(n=Count('id')))
            total = sum(counts.values())
            sites.append({'facility': facility, 'total': total, 'counts': counts,
                          'retention': round(100 * counts.get('active', 0) / total, 1) if total else None})
        totals = dict(enrollments.values_list('status').annotate(n=Count('id')))
        assessed = StudyEligibilityAssessment.objects.filter(study=study, patient__current_facility__in=f.facilities,
                                                             assessed_on__range=(f.start, f.end))
        consents = ConsentRecord.objects.filter(study=study, patient__current_facility__in=f.facilities)
        first = enrollments.order_by('enrollment_date').values_list('enrollment_date', flat=True).first()
        monthly, cumulative = [], 0
        if first:
            per_month = dict(enrollments.annotate(m=TruncMonth('enrollment_date')).values_list('m').annotate(n=Count('id')))
            for month in month_range(first, f.end):
                n = per_month.get(month, 0)
                cumulative += n
                if month >= f.start.replace(day=1):
                    monthly.append((month, n, cumulative))
        ctx.update(sites=sites, statuses=StudyEnrollment.Status.choices, totals=totals, total=sum(totals.values()),
                   assessed=assessed.count(), eligible=assessed.filter(is_eligible=True).count(),
                   consented=consents.filter(decision='consented').count(),
                   consent_withdrawn=consents.filter(decision='withdrawn').count(), monthly=monthly[-24:])
        return ctx


class FollowUpReportView(ReportPage):
    """Monthly follow-up: appointments due and attended, missed visits, LTFU, returns and deaths."""

    report_title = 'Follow-up & retention'
    body_template = 'reporting/body/follow_up.html'
    default_period = 'last_12'

    def get_context_data(self, **kwargs):
        from clinical.models import EnrollmentEvent
        from encounters.models import Appointment, Encounter

        f = self.get_filters()
        start = max(f.start.replace(day=1), add_months(f.end.replace(day=1), -35))
        appointments = Appointment.objects.filter(scheduled_date__gte=start, scheduled_date__lte=f.end,
                                                  patient__current_facility__in=f.facilities, patient__is_deleted=False)
        events = EnrollmentEvent.objects.filter(event_date__gte=start, event_date__lte=f.end,
                                                enrollment__programme__disease__is_active=True,
                                                enrollment__patient__current_facility__in=f.facilities)
        deaths = Patient.objects.filter(current_facility__in=f.facilities, date_of_death__gte=start, date_of_death__lte=f.end)
        unscheduled = Encounter.objects.filter(stage='unscheduled', encounter_date__date__gte=start,
                                               encounter_date__date__lte=f.end, patient__current_facility__in=f.facilities,
                                               patient__is_deleted=False).exclude(status='cancelled')
        if f.disease:
            appointments = appointments.filter(patient__programme_enrollments__programme__disease=f.disease).distinct()
            unscheduled = unscheduled.filter(patient__programme_enrollments__programme__disease=f.disease).distinct()
            events = events.filter(enrollment__programme__disease=f.disease)
            deaths = deaths.filter(programme_enrollments__programme__disease=f.disease).distinct()
        per_month = OrderedDict((m, Counter()) for m in month_range(start, f.end))
        for month, status, n in (appointments.annotate(m=TruncMonth('scheduled_date')).values_list('m', 'status')
                                 .annotate(n=Count('id'))):
            per_month[month][status] += n
        for month, kind, to_status, n in (events.annotate(m=TruncMonth('event_date')).values_list('m', 'kind', 'to_status')
                                          .annotate(n=Count('enrollment__patient', distinct=True))):
            if to_status == 'ltfu':
                per_month[month]['_ltfu'] += n
            if kind in ('return', 'transfer_in'):
                per_month[month]['_return'] += n
        for month, n in deaths.annotate(m=TruncMonth('date_of_death')).values_list('m').annotate(n=Count('id')):
            if month in per_month:
                per_month[month]['_death'] += n
        for month, n in (unscheduled.annotate(m=TruncMonth('encounter_date', output_field=DateField())).values_list('m')
                         .annotate(n=Count('id', distinct=True))):
            if month in per_month:
                per_month[month]['_unscheduled'] += n
        rows = []
        for month, c in per_month.items():
            attended, missed = c['attended'], c['missed']
            rows.append({'month': month, 'due': sum(v for k, v in c.items() if not k.startswith('_')),
                         'attended': attended, 'missed': missed, 'cancelled': c['cancelled'],
                         'open': c['scheduled'] + c['confirmed'] + c['rescheduled'] + c['expected'],
                         'rate': round(100 * attended / (attended + missed), 1) if attended + missed else None,
                         'unscheduled': c['_unscheduled'], 'ltfu': c['_ltfu'], 'returned': c['_return'],
                         'deaths': c['_death']})
        return super().get_context_data(rows=rows, ltfu_months=settings.LTFU_MONTHS, **kwargs)

    def csv_rows(self, context):
        yield ['Month', 'Visits due', 'Attended', 'Missed', 'Cancelled', 'Still open', 'Attendance %',
               'Unscheduled visits', 'Became LTFU', 'Returned to care', 'Deaths']
        for r in context['rows']:
            yield [f'{r["month"]:%Y-%m}', r['due'], r['attended'], r['missed'], r['cancelled'], r['open'], r['rate'],
                   r['unscheduled'], r['ltfu'], r['returned'], r['deaths']]


class DataQualityReportView(ReportPage):
    report_title = 'Data quality'
    body_template = 'reporting/body/data_quality.html'
    default_period = 'all'
    with_disease = False

    def get_context_data(self, **kwargs):
        from django.db.models import Q

        from issues.models import DataIssue

        f = self.get_filters()
        issues = DataIssue.objects.in_scope(self.request.user).filter(
            Q(facility__in=f.facilities) | Q(facility__isnull=True), created_at__date__range=(f.start, f.end))
        by_category = []
        for value, label in DataIssue.Category.choices:
            row = dict(issues.filter(category=value).values_list('status').annotate(n=Count('id')))
            if row:
                by_category.append((value, label, row.get('open', 0), row.get('resolved', 0), row.get('dismissed', 0)))
        by_code = issues.open().values('code').annotate(n=Count('id')).order_by('-n')
        resolved_by = (issues.exclude(status='open').values('resolved_by__username').annotate(n=Count('id')).order_by('-n')[:10])
        total = issues.count()
        closed = issues.exclude(status='open').count()
        return super().get_context_data(by_category=by_category, by_code=by_code, resolved_by=resolved_by, total=total,
                                        closed=closed, progress=round(100 * closed / total, 1) if total else None, **kwargs)
