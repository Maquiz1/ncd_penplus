"""The EDCS-style reports: recruitment by site, patients by condition, vitals and HbA1c trends."""
from collections import Counter, OrderedDict, defaultdict
from decimal import Decimal

from django.db.models import Count, Q
from django.utils import timezone

from clinical.models import Diagnosis, EnrollmentEvent, ProgrammeEnrollment, Screening
from core.utils import add_months
from encounters.models import Vitals
from laboratory.models import LabOrder
from patients.models import Patient, with_old_study_id
from research.models import StudyEligibilityAssessment

AGE_GROUPS = [('<5', 0, 4), ('5–17', 5, 17), ('18–29', 18, 29), ('30–44', 30, 44), ('45–59', 45, 59), ('60+', 60, 200)]
ENDED = {'ltfu': 'LTFU', 'transferred_out': 'Transferred out', 'deceased': 'Died', 'withdrawn': 'Withdrawn',
         'completed': 'Completed', 'other': 'Other'}


def age_group(age):
    if age is None:
        return 'Unknown'
    return next(label for label, low, high in AGE_GROUPS if low <= age <= high)


def age_on(patient, date):
    dob = patient.date_of_birth
    if dob is None:
        return None
    return date.year - dob.year - ((date.month, date.day) < (dob.month, dob.day))


# ------------------------------------------------------------------ recruitment status by site

def recruitment_by_site(filters):
    start, end, disease = filters.start, filters.end, filters.disease
    diseases = [disease] if disease else list(filters.diseases)
    patients = Patient.objects.filter(current_facility__in=filters.facilities)
    enrollments = ProgrammeEnrollment.objects.filter(facility__in=filters.facilities,
                                                     programme__disease__in=diseases, patient__is_deleted=False)
    screenings = Screening.objects.filter(facility__in=filters.facilities, disease__in=diseases,
                                          screening_date__range=(start, end), patient__is_deleted=False)
    events = EnrollmentEvent.objects.filter(enrollment__in=enrollments, event_date__range=(start, end),
                                            to_status__in=ENDED)
    eligibility = StudyEligibilityAssessment.objects.filter(patient__current_facility__in=filters.facilities,
                                                            patient__is_deleted=False)
    rows = []
    for facility in filters.facilities.order_by('name'):
        site_enrollments = enrollments.filter(facility=facility)
        new = site_enrollments.filter(enrollment_date__range=(start, end))
        ended = Counter(dict(events.filter(enrollment__facility=facility).values_list('to_status')
                             .annotate(n=Count('enrollment__patient', distinct=True))))
        row = {
            'facility': facility,
            'registered': patients.filter(registration_facility=facility, registration_date__range=(start, end)).count(),
            'screened': screenings.filter(facility=facility).values('patient').distinct().count(),
            'positive': screenings.filter(facility=facility, result='positive').values('patient').distinct().count(),
            'eligible': eligibility.filter(patient__current_facility=facility, is_eligible=True,
                                           assessed_on__range=(start, end)).values('patient').distinct().count(),
            'enrolled': new.values('patient').distinct().count(),
            'by_disease': [new.filter(programme__disease=d).values('patient').distinct().count() for d in diseases],
            'ended': [ended.get(code, 0) for code in ENDED],
            'active': site_enrollments.filter(status='active').values('patient').distinct().count(),
        }
        if row['registered'] or row['screened'] or row['enrolled'] or row['active'] or any(row['ended']):
            rows.append(row)
    total = {key: sum(r[key] for r in rows) for key in ('registered', 'screened', 'positive', 'eligible', 'enrolled', 'active')}
    total['by_disease'] = [sum(r['by_disease'][i] for r in rows) for i in range(len(diseases))]
    total['ended'] = [sum(r['ended'][i] for r in rows) for i in range(len(ENDED))]
    return {'rows': rows, 'total': total, 'diseases': diseases, 'ended_labels': list(ENDED.values())}


# ------------------------------------------------------------------ patients by condition

def condition_report(filters, disease, condition_code=None):
    """Patients in care for `disease` by main condition (sub-types roll up), with site, sex and age group."""
    end = filters.end
    enrollments = ProgrammeEnrollment.objects.filter(programme__disease=disease, facility__in=filters.facilities,
                                                     enrollment_date__lte=end, patient__is_deleted=False)
    status = {}
    for patient_id, st in enrollments.order_by('enrollment_date').values_list('patient_id', 'status'):
        status[patient_id] = 'active' if st == 'active' or status.get(patient_id) == 'active' else st
    patients = {p.pk: p for p in with_old_study_id(Patient.objects.filter(pk__in=status)
                                                   .select_related('current_facility'))}
    diagnoses = (Diagnosis.objects.filter(patient_id__in=patients, disease=disease, diagnosis_date__lte=end)
                 .exclude(status='ruled_out').select_related('condition__parent__parent'))
    conditions_of = defaultdict(dict)
    for dx in diagnoses:
        root = dx.condition.root if dx.condition_id else None
        key = root.code if root else 'unlinked'
        label = root.name if root else 'Diagnosis not linked to a condition'
        entry = conditions_of[dx.patient_id].setdefault(key, {'label': label, 'subtypes': set(), 'first': dx.diagnosis_date})
        if dx.condition_id and dx.condition.parent_id:
            entry['subtypes'].add(dx.condition.name)
        entry['first'] = min(entry['first'], dx.diagnosis_date)
    for pid in patients:
        if not conditions_of[pid]:
            conditions_of[pid]['none'] = {'label': f'{disease.name}, diagnosis not yet recorded', 'subtypes': set(),
                                          'first': None}
    sites = list(filters.facilities.filter(pk__in={p.current_facility_id for p in patients.values()}).order_by('name'))
    groups = OrderedDict()
    for pid, conds in conditions_of.items():
        patient = patients[pid]
        age = age_on(patient, end)
        for key, info in conds.items():
            g = groups.setdefault(key, {'code': key, 'label': info['label'], 'patients': [], 'sites': Counter(),
                                        'sex': Counter(), 'ages': Counter(), 'active': 0, 'new': 0})
            g['patients'].append({'patient': patient, 'age': age, 'subtypes': sorted(info['subtypes']),
                                  'status': status[pid], 'first': info['first']})
            g['sites'][patient.current_facility_id] += 1
            g['sex'][patient.sex] += 1
            g['ages'][age_group(age)] += 1
            g['active'] += status[pid] == 'active'
            g['new'] += bool(info['first'] and filters.start <= info['first'] <= end)
    order = {c.code: c.order for c in disease.conditions.filter(parent__isnull=True)}
    rows = sorted(groups.values(), key=lambda g: (g['code'] in ('none', 'unlinked'), order.get(g['code'], 99), g['label']))
    for g in rows:
        g['site_counts'] = [g['sites'].get(f.pk, 0) for f in sites]
        g['age_counts'] = [g['ages'].get(label, 0) for label, _l, _h in AGE_GROUPS]
        g['total'] = len(g['patients'])
    selected = next((g for g in rows if g['code'] == condition_code), None)
    if selected:
        selected['patients'].sort(key=lambda r: r['patient'].patient_number)
    return {'rows': rows, 'sites': sites, 'age_labels': [a[0] for a in AGE_GROUPS], 'patients_total': len(patients),
            'selected': selected,
            'multi': sum(1 for conds in conditions_of.values() if len([k for k in conds if k != 'none']) > 1)}


# ------------------------------------------------------------------ vitals and HbA1c trends

def months_between(start, end, cap=24):
    months, month = [], max(start.replace(day=1), add_months(end.replace(day=1), -(cap - 1)))
    while month <= end:
        months.append(month)
        month = add_months(month, 1)
    return months


def _mean(values):
    return round(sum(values) / len(values), 1) if values else None


def _pct(part, whole):
    return round(100 * part / whole, 1) if whole else None


def trends_report(filters):
    start, end = filters.start, filters.end
    months = months_between(start, end)
    first = months[0]
    patient_filter = Q(current_facility__in=filters.facilities, is_deleted=False)
    if filters.disease_ids:
        patient_filter &= Q(programme_enrollments__programme__disease__in=filters.disease_ids)
    patient_ids = set(Patient.objects.filter(patient_filter).values_list('pk', flat=True))
    bp = defaultdict(dict)       # month -> patient -> (sbp, dbp) last reading in the month
    counts = Counter()
    for pid, when, sbp, dbp in (Vitals.objects.filter(encounter__patient_id__in=patient_ids, taken_at__date__gte=first,
                                                      taken_at__date__lte=end)
                                .order_by('taken_at').values_list('encounter__patient_id', 'taken_at', 'systolic_bp', 'diastolic_bp')):
        month = timezone.localtime(when).date().replace(day=1)
        counts[month] += 1
        if sbp and dbp:
            bp[month][pid] = (sbp, dbp)
    a1c = defaultdict(dict)
    glucose = defaultdict(list)
    labs = (LabOrder.objects.filter(patient_id__in=patient_ids, test__code__in=['hba1c', 'rbg', 'fbg'],
                                    status__in=['resulted', 'reviewed'], result_numeric__isnull=False,
                                    resulted_at__date__gte=first, resulted_at__date__lte=end)
            .order_by('resulted_at').values_list('patient_id', 'test__code', 'resulted_at', 'result_numeric'))
    for pid, code, when, value in labs:
        month = timezone.localtime(when).date().replace(day=1)
        if code == 'hba1c':
            a1c[month][pid] = value
        else:
            glucose[month].append(value)
    rows = []
    for month in months:
        readings = list(bp[month].values())
        a1c_values = list(a1c[month].values())
        rows.append({
            'month': month, 'vitals': counts[month], 'bp_patients': len(readings),
            'sbp': _mean([s for s, _d in readings]), 'dbp': _mean([d for _s, d in readings]),
            'bp_controlled': _pct(sum(1 for s, d in readings if s < 140 and d < 90), len(readings)),
            'a1c_patients': len(a1c_values), 'a1c_mean': _mean(a1c_values),
            'a1c_controlled': _pct(sum(1 for v in a1c_values if v < Decimal('8')), len(a1c_values)),
            'glucose_tests': len(glucose[month]), 'glucose_mean': _mean(glucose[month]),
        })
    return {'rows': rows, 'patients': len(patient_ids)}


# ------------------------------------------------------------------ one disease in detail

def disease_report(filters, disease, user):
    """Everything about one disease programme: status, sites × conditions, sub-types, sex/age and indicators."""
    from indicators import clinical
    from indicators.engine import Cohort

    start, end = filters.start, filters.end
    conditions = condition_report(filters, disease)
    sites = conditions['sites']
    enrollments = ProgrammeEnrollment.objects.filter(programme__disease=disease, facility__in=filters.facilities,
                                                     enrollment_date__lte=end, patient__is_deleted=False)
    status = Counter(dict(enrollments.values_list('status').annotate(n=Count('patient', distinct=True))))
    tiles = [('Ever enrolled', enrollments.values('patient').distinct().count(), 'secondary'),
             ('Active', status.get('active', 0), 'success'),
             ('New in period', enrollments.filter(enrollment_date__range=(start, end)).values('patient').distinct().count(),
              'primary'),
             ('Lost to follow-up', status.get('ltfu', 0), 'warning'),
             ('Transferred out', status.get('transferred_out', 0), 'info'),
             ('Died', status.get('deceased', 0), 'danger')]

    # sites as rows, main conditions as columns (like the EDCS report)
    by_site = []
    for i, site in enumerate(sites):
        site_enrollments = enrollments.filter(facility=site)
        by_site.append({'site': site, 'enrolled': site_enrollments.values('patient').distinct().count(),
                        'active': site_enrollments.filter(status='active').values('patient').distinct().count(),
                        'cells': [g['site_counts'][i] for g in conditions['rows']]})
    site_total = {'enrolled': sum(r['enrolled'] for r in by_site), 'active': sum(r['active'] for r in by_site),
                  'cells': [g['total'] for g in conditions['rows']]}

    # sub-types of each main condition (e.g. cardiomyopathy: ischaemic, alcohol-related…) by site
    subtypes = []
    for g in conditions['rows']:
        counts = defaultdict(Counter)
        for row in g['patients']:
            for name in row['subtypes'] or ['Type not specified']:
                counts[name][row['patient'].current_facility_id] += 1
        if len(counts) > 1 or (counts and 'Type not specified' not in counts):
            names = sorted(counts, key=lambda n: (n == 'Type not specified', n))
            subtypes.append({'label': g['label'], 'names': names,
                             'rows': [(site, [counts[n][site.pk] for n in names]) for site in sites],
                             'total': [sum(counts[n].values()) for n in names]})

    cohort = Cohort(user, facilities=filters.facilities, disease=disease)
    indicators = [(i, i.evaluate(cohort, end)) for i in clinical.visible(clinical.INDICATORS, {disease.code})]
    return {'tiles': tiles, 'conditions': conditions, 'by_site': by_site, 'site_total': site_total,
            'subtypes': subtypes, 'indicators': indicators}
