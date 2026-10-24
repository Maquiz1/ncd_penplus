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
from patients.models import Patient

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


class ClinicalView(IndicatorPermission, View):
    template = 'indicators/clinical.html'
    socio = False

    def get(self, request):
        date = selected_date(request)
        facility = selected_facility(request)
        cohort = Cohort(request.user, facility)
        active = {d.code: d for d in Disease.objects.filter(is_active=True)}
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
                                ['Area', 'Indicator', 'Numerator / value', 'Unit', 'Denominator / patients', '%'], rows)
        groups = []
        for code, disease in active.items():
            items = [(i, r) for i, r in results if i.disease == code]
            if items:
                groups.append((disease.name, items))
        if self.socio:
            groups = [('Sociodemographic', results)]
        return render(request, self.template, {
            'groups': groups, 'date': date, 'facility': facility, 'facilities': clinic_choices(request.user),
            'socio': self.socio, 'scope': scope_description(request.user), 'generated': timezone.now(),
            'inactive': Disease.objects.filter(is_active=False)})


class SociodemographicView(ClinicalView):
    socio = True


class DrillView(IndicatorPermission, View):
    """The patients behind a number."""

    def get(self, request):
        facility = selected_facility(request)
        cohort = Cohort(request.user, facility)
        if request.GET.get('source') == 'core':
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
            date = selected_date(request)
            part = 'numerator' if request.GET.get('part') == 'numerator' else 'denominator'
            ids = indicator.evaluate(cohort, date)[part]
            title = f'{indicator.label} – {part} (as of {date:%d %b %Y})'
            back = reverse('indicators:socio' if indicator.disease == 'socio' else 'indicators:clinical') + f'?date={date}'
        patients = Patient.objects.filter(pk__in=ids).select_related('current_facility').order_by('patient_number')
        return render(request, 'indicators/drill.html', {'title': title, 'patients': patients, 'back': back})
