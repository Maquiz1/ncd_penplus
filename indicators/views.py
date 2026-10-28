import csv
import datetime

from django.contrib.auth.mixins import PermissionRequiredMixin
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views import View

from audit.models import AuditEvent
from audit.services import log_event
from clinical.models import Disease, ProgrammeEnrollment
from facilities.models import Facility
from facilities.scope import facilities_for_user, scope_description
from patients.models import Patient, with_old_study_id
from reporting.filters import ReportFilters

from . import clinical, core
from .engine import Cohort, quarter_bounds


def clinic_choices(user):
    ids = ProgrammeEnrollment.objects.filter(programme__disease__is_active=True).values_list('facility_id', flat=True)
    return facilities_for_user(user).filter(pk__in=ids).order_by('name')


def selected_facility(request):
    pk = request.GET.get('facility')
    return clinic_choices(request.user).filter(pk=pk).first() if pk and pk.isdigit() else None


def selected_date(request):
    try:
        return datetime.date.fromisoformat(request.GET.get('date', ''))
    except ValueError:
        return timezone.localdate()


def csv_response(filename, header, rows):
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    response.write('﻿')  # Excel opens UTF-8 correctly with a BOM
    writer = csv.writer(response)
    writer.writerow(header)
    writer.writerows(rows)
    return response


class IndicatorPermission(PermissionRequiredMixin):
    permission_required = 'reporting.view_reports'


class QuarterlyView(IndicatorPermission, View):
    """Core indicators laid out like the PEN-Plus quarterly reporting form: Q1–Q4 and year to date."""

    def get(self, request):
        today = timezone.localdate()
        year = int(request.GET.get('year') or today.year)
        facility = selected_facility(request)
        cohort = Cohort(request.user, facility)
        table = core.year_table(cohort, year)
        if request.GET.get('format') == 'csv':
            log_event(request=request, action=AuditEvent.Action.EXPORT, model='indicators.Quarterly',
                      object_repr=f'PEN-Plus core indicators {year} {facility or "all clinics"}')

            def cell(c):
                return '' if c is None else (f'{c["pct"]}%' if c.get('pct') is not None else c['n'])
            return csv_response(f'penplus-core-indicators-{year}.csv',
                                ['Code', 'Indicator', f'Q1 {year}', f'Q2 {year}', f'Q3 {year}', f'Q4 {year}', 'YTD'],
                                [[row['code'], ('  ' * row['indent']) + row['label'], *[cell(c) for c in cells], cell(ytd)]
                                 for row, cells, ytd in table])
        return render(request, 'indicators/quarterly.html', {
            'table': table, 'year': year, 'years': range(today.year, 2022, -1), 'facility': facility,
            'facilities': clinic_choices(request.user), 'scope': scope_description(request.user),
            'quarters': [(q, *quarter_bounds(year, q)) for q in (1, 2, 3, 4)], 'generated': timezone.now(),
            'active_diseases': Disease.objects.filter(is_active=True)})


def indicator_filters(request):
    """Area, period and disease filters; indicators are measured on the last day of the period."""
    filters = ReportFilters(request, default_period='this_month')
    return filters, Cohort(request.user, facilities=filters.facilities, disease=filters.disease)


class ClinicalView(IndicatorPermission, View):
    template = 'indicators/clinical.html'
    socio = False

    def get(self, request):
        filters, cohort = indicator_filters(request)
        date, facility = filters.end, filters.area_label
        active = {d.code: d for d in filters.diseases}
        if filters.disease and not self.socio:
            active = {filters.disease.code: filters.disease}
        source = clinical.SOCIODEMOGRAPHIC if self.socio else clinical.visible(clinical.INDICATORS, set(active))
        results = [(i, i.evaluate(cohort, date)) for i in source]
        if request.GET.get('format') == 'csv':
            log_event(request=request, action=AuditEvent.Action.EXPORT, model='indicators.Clinical',
                      object_repr=f'{"Sociodemographic" if self.socio else "Clinical"} indicators {date} {facility or "all"}')
            rows = []
            for i, r in results:
                if i.kind == 'avg':
                    rows.append([active[i.disease].name if i.disease in active else 'Sociodemographic', i.label,
                                 r['value'] if r['value'] is not None else '', i.unit, r['n'], ''])
                else:
                    rows.append([active[i.disease].name if i.disease in active else 'Sociodemographic', i.label,
                                 len(r['numerator']), '', len(r['denominator']), '' if r['pct'] is None else r['pct']])
            return csv_response(f'penplus-{"socio" if self.socio else "clinical"}-indicators-{date}.csv',
                                ['Disease', 'Indicator', 'Numerator / value', 'Unit', 'Denominator / patients', '%'],
                                [[f'{filters.description} (measured on {date:%d %b %Y})']] + rows)
        groups = []
        for code, disease in active.items():
            items = [(i, r) for i, r in results if i.disease == code]
            if items:
                groups.append((disease.name, items))
        if self.socio:
            groups = [('Sociodemographic', results)]
        return render(request, self.template, {
            'groups': groups, 'date': date, 'filters': filters, 'hidden_filters': {}, 'hide_pdf': True,
            'csv_available': True, 'socio': self.socio, 'scope': scope_description(request.user),
            'generated': timezone.now(), 'inactive': Disease.objects.filter(is_active=False)})


class SociodemographicView(ClinicalView):
    socio = True


PARTS = {'numerator': 'meeting the indicator', 'denominator': 'all eligible patients',
         'missed': 'not meeting the indicator (to follow up)'}


class DrillView(IndicatorPermission, View):
    """The patients behind a number. For clinical indicators, 'missed' lists eligible patients not meeting it."""

    def get(self, request):
        last_visit, subtitle = None, ''
        if request.GET.get('source') == 'core':
            facility = selected_facility(request)
            cohort = Cohort(request.user, facility)
            year, quarter, code = int(request.GET['year']), int(request.GET['q']), request.GET.get('code', '')
            row, ids = core.drill(cohort, year, quarter, code)
            if row is None:
                raise Http404
            title = f'{row["code"]} – {row["label"]} (Q{quarter} {year})'
            back = reverse('indicators:quarterly') + f'?year={year}' + (f'&facility={facility.pk}' if facility else '')
        else:
            indicator = clinical.find(request.GET.get('key', ''))
            if indicator is None:
                raise Http404
            filters, cohort = indicator_filters(request)
            date = filters.end
            part = request.GET.get('part') if request.GET.get('part') in PARTS else 'denominator'
            result = indicator.evaluate(cohort, date)
            ids = result['denominator'] - result['numerator'] if part == 'missed' else result[part]
            title = f'{indicator.label} – {PARTS[part]}'
            subtitle = f'{filters.description} · measured on {date:%d %b %Y}'
            back = reverse('indicators:socio' if indicator.disease == 'socio' else 'indicators:clinical') + '?' + filters.querystring()
            last_visit = {pid: cohort.last_visit(pid, date) for pid in ids}
        patients = list(with_old_study_id(Patient.objects.filter(pk__in=ids).select_related('current_facility')
                                          .order_by('patient_number')))
        today = timezone.localdate()
        for p in patients:
            p.last_visit = last_visit.get(p.pk) if last_visit is not None else None
            p.days_since_visit = (today - p.last_visit).days if p.last_visit else None
        if last_visit is not None and request.GET.get('format') == 'csv':
            log_event(request=request, action=AuditEvent.Action.EXPORT, model='indicators.Drill', object_repr=title)
            return csv_response(f'indicator-{indicator.key}-{part}-{date}.csv',
                                ['Patient ID', 'Old ID', 'Sex', 'Age', 'Facility', 'Last visit', 'Days since last visit'],
                                [[p.patient_number, p.old_study_id or p.old_id or '', p.get_sex_display(), p.age, p.current_facility,
                                  p.last_visit or '', '' if p.days_since_visit is None else p.days_since_visit]
                                 for p in patients])
        return render(request, 'indicators/drill.html', {
            'title': title, 'subtitle': subtitle, 'patients': patients, 'back': back,
            'with_visits': last_visit is not None, 'csv_url': '?' + request.GET.urlencode() + '&format=csv'
            if last_visit is not None else ''})
