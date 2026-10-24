"""Patient cohorts for indicators: who was enrolled, active, LTFU, transferred or dead on any date, and with which
reporting conditions. Only ACTIVE diseases and conditions are counted. Status on a past date comes from the
enrollment history (EnrollmentEvent), so earlier quarters stay correct when patients later change status."""
import datetime
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from django.db.models import Q
from django.utils import timezone

from clinical.models import Diagnosis, ProgrammeEnrollment, Screening
from core.utils import add_months
from crf.models import FormSubmission
from diseases.models import Condition
from encounters.models import Admission, Encounter
from facilities.scope import facilities_for_user
from investigations.models import InvestigationOrder
from laboratory.models import LabOrder
from pharmacy.models import MedicationRecord, Prescription

OPEN = ('active', 'pending')
MISSING_CODES = {'', '98', '99', '98.0', '99.0', '999', '9999', '-', 'nil', 'none', 'n/a'}


def number(value):
    text = str(value if value is not None else '').strip().casefold()
    if text in MISSING_CODES:
        return None
    try:
        return Decimal(text.replace(',', ''))
    except (InvalidOperation, ValueError):
        return None


def quarter_bounds(year, quarter):
    start = datetime.date(year, 3 * quarter - 2, 1)
    return start, add_months(start, 3) - datetime.timedelta(days=1)


def age_on(dob, date):
    return date.year - dob.year - ((date.month, date.day) < (dob.month, dob.day))


class Cohort:
    def __init__(self, user, facility=None):
        facilities = facilities_for_user(user)
        if facility is not None:
            facilities = facilities.filter(pk=facility.pk)
        self.facility_ids = list(facilities.values_list('pk', flat=True))
        self.enrollments = list(
            ProgrammeEnrollment.objects.filter(programme__disease__is_active=True, facility_id__in=self.facility_ids,
                                               patient__is_deleted=False)
            .select_related('patient', 'programme__disease').prefetch_related('events'))
        self.by_patient = defaultdict(list)
        for e in self.enrollments:
            self.by_patient[e.patient_id].append(e)
        self.patients = {e.patient_id: e.patient for e in self.enrollments}
        all_conditions = {c.pk: c for c in Condition.objects.filter(is_active=True, disease__is_active=True)
                          .select_related('disease', 'parent').prefetch_related('excludes')}
        self.root_of = {pk: (c.parent_id or pk) for pk, c in all_conditions.items()}
        self.conditions = {pk: c for pk, c in all_conditions.items() if c.parent_id is None}
        self.defaults = {c.disease_id: c.pk for c in self.conditions.values() if c.is_default}
        self.diagnoses = defaultdict(list)
        for pid, cid, date in (Diagnosis.objects.filter(patient_id__in=self.patients, condition_id__in=self.root_of)
                               .exclude(status='ruled_out').values_list('patient_id', 'condition_id', 'diagnosis_date')):
            if self.root_of.get(cid) in self.conditions:
                self.diagnoses[pid].append((self.root_of[cid], date))
        self._cache = {}

    # ---------------------------------------------------------------- status on a date
    @staticmethod
    def enrollment_status(enrollment, date):
        if enrollment.enrollment_date > date:
            return None
        events = sorted(enrollment.events.all(), key=lambda e: (e.event_date, e.pk))
        if events:
            status = 'active'
            for event in events:
                if event.event_date <= date:
                    status = event.to_status
            return status
        if enrollment.status not in OPEN and (enrollment.outcome_date is None or enrollment.outcome_date <= date):
            return enrollment.status
        return 'active'

    def ever_enrolled(self, date):
        return {pid for pid, enrollments in self.by_patient.items() if any(e.enrollment_date <= date for e in enrollments)}

    def active(self, date):
        key = ('active', date)
        if key not in self._cache:
            result = set()
            for pid, enrollments in self.by_patient.items():
                patient = self.patients[pid]
                if patient.date_of_death and patient.date_of_death <= date:
                    continue
                if any(self.enrollment_status(e, date) in OPEN for e in enrollments):
                    result.add(pid)
            self._cache[key] = result
        return self._cache[key]

    def first_enrollment(self, pid):
        return min(e.enrollment_date for e in self.by_patient[pid])

    def became(self, status, start, end):
        """Patients with a status change to `status` in [start, end] (e.g. new LTFU in the quarter)."""
        return {e.patient_id for e in self.enrollments for ev in e.events.all()
                if ev.to_status == status and start <= ev.event_date <= end}

    def died(self, start, end):
        return {pid for pid, p in self.patients.items() if p.date_of_death and start <= p.date_of_death <= end}

    # ---------------------------------------------------------------- conditions
    def conditions_of(self, pid, date):
        ids = {cid for cid, d in self.diagnoses.get(pid, []) if d is None or d <= date}
        diseases_with_condition = {self.conditions[c].disease_id for c in ids}
        for e in self.by_patient.get(pid, []):
            disease_id = e.programme.disease_id
            if e.enrollment_date <= date and disease_id not in diseases_with_condition and disease_id in self.defaults:
                ids.add(self.defaults[disease_id])
        return {c for c in ids if not any(x.pk in ids for x in self.conditions[c].excludes.all())}

    def with_condition(self, pids, codes, date):
        wanted = {c.pk for c in self.conditions.values() if c.code in codes}
        return {pid for pid in pids if self.conditions_of(pid, date) & wanted}

    def with_disease(self, pids, disease_code, date):
        return {pid for pid in pids if any(self.conditions[c].disease.code == disease_code for c in self.conditions_of(pid, date))}

    # ---------------------------------------------------------------- clinical data (loaded once per cohort)
    def encounter_dates(self):
        if 'encounters' not in self._cache:
            dates = defaultdict(list)
            for pid, when in Encounter.objects.filter(patient_id__in=self.patients).exclude(status='cancelled').values_list(
                    'patient_id', 'encounter_date'):
                dates[pid].append(timezone.localtime(when).date())
            self._cache['encounters'] = dates
        return self._cache['encounters']

    def crf(self, key):
        """{patient: [(date, value), …]} for a form variable, from any form that uses that variable name."""
        cache_key = ('crf', key)
        if cache_key not in self._cache:
            values = defaultdict(list)
            for pid, data, submitted, encounter_date in (
                    FormSubmission.objects.filter(patient_id__in=self.patients, data__has_key=key)
                    .exclude(status__in=['in_progress', 'not_applicable'])
                    .values_list('patient_id', 'data', 'submitted_at', 'encounter__encounter_date')):
                when = encounter_date or submitted
                if when is not None:
                    values[pid].append((timezone.localtime(when).date(), str(data.get(key, '')).strip()))
            for items in values.values():
                items.sort()
            self._cache[cache_key] = values
        return self._cache[cache_key]

    def latest_crf(self, pid, key, date, since=None):
        items = [(d, v) for d, v in self.crf(key).get(pid, []) if d <= date and (since is None or d >= since) and v]
        return items[-1][1] if items else None

    def any_crf(self, pid, key, date, since=None, test=lambda v: v == '1'):
        return any(test(v) for d, v in self.crf(key).get(pid, []) if d <= date and (since is None or d >= since))

    def labs(self):
        if 'labs' not in self._cache:
            results = defaultdict(list)
            for pid, code, when, value, numeric, abnormal, category in (
                    LabOrder.objects.filter(patient_id__in=self.patients, status__in=['resulted', 'reviewed'])
                    .values_list('patient_id', 'test__code', 'resulted_at', 'result_value', 'result_numeric',
                                 'is_abnormal', 'test__category__name')):
                if when:
                    results[pid].append((timezone.localtime(when).date(), code, value, numeric, abnormal, category))
            for items in results.values():
                items.sort(key=lambda r: r[0])
            self._cache['labs'] = results
        return self._cache['labs']

    def lab(self, pid, code, date, since=None):
        return [r for r in self.labs().get(pid, []) if r[1] == code and r[0] <= date and (since is None or r[0] >= since)]

    def investigations(self, pid, modality, date, since=None):
        if 'investigations' not in self._cache:
            items = defaultdict(list)
            for p, mod, when, abnormal in (InvestigationOrder.objects.filter(patient_id__in=self.patients, status__in=['completed', 'reviewed'])
                                           .values_list('patient_id', 'modality', 'performed_at', 'is_abnormal')):
                if when:
                    items[p].append((timezone.localtime(when).date(), mod, abnormal))
            self._cache['investigations'] = items
        return [i for i in self._cache['investigations'].get(pid, []) if i[1] == modality and i[0] <= date
                and (since is None or i[0] >= since)]

    def admitted(self, pid, start, end):
        if 'admissions' not in self._cache:
            items = defaultdict(list)
            for p, when in Admission.objects.filter(patient_id__in=self.patients).values_list('patient_id', 'admitted_at'):
                items[p].append(timezone.localtime(when).date())
            self._cache['admissions'] = items
        return any(start <= d <= end for d in self._cache['admissions'].get(pid, []))

    def meds_at_last_visit(self, pid, date):
        """Medicines the patient was on at their last recorded visit up to `date` (history + recent prescriptions)."""
        if 'meds' not in self._cache:
            history = defaultdict(list)
            for p, name, action, when in MedicationRecord.objects.filter(patient_id__in=self.patients).values_list(
                    'patient_id', 'medication_name', 'action', 'record_date'):
                history[p].append((when, name.casefold(), action))
            prescriptions = defaultdict(list)
            for p, name, strength, when in (Prescription.objects.filter(patient_id__in=self.patients).exclude(status='cancelled')
                                            .values_list('patient_id', 'medication__name', 'medication__strength', 'prescribed_at')):
                prescriptions[p].append((timezone.localtime(when).date(), f'{name} {strength}'.casefold()))
            self._cache['meds'] = (history, prescriptions)
        history, prescriptions = self._cache['meds']
        records = [r for r in history.get(pid, []) if r[0] <= date]
        names = set()
        if records:
            last = max(r[0] for r in records)
            names |= {name for when, name, action in records if when == last and action not in ('stop', 'not_eligible')}
        names |= {name for when, name in prescriptions.get(pid, []) if date - datetime.timedelta(days=92) <= when <= date}
        return names

    def on_any(self, pid, date, keywords):
        return any(k in name for name in self.meds_at_last_visit(pid, date) for k in keywords)

    def cardiomyopathy(self):
        if 'cardiomyopathy' not in self._cache:
            self._cache['cardiomyopathy'] = set(Diagnosis.objects.filter(
                patient_id__in=self.patients, description__icontains='cardiomyopathy').exclude(status='ruled_out')
                .values_list('patient_id', flat=True))
        return self._cache['cardiomyopathy']

    def scd_tests(self, start, end):
        """SCD tests in the period (screenings for SCD and sickle-cell lab tests): (tests, positive, patients)."""
        tests, positive, patients = 0, 0, set()
        for pid, result in Screening.objects.filter(disease__code='scd', facility_id__in=self.facility_ids,
                                                    screening_date__range=(start, end)).values_list('patient_id', 'result'):
            tests += 1
            positive += result == 'positive'
            patients.add(pid)
        for pid, abnormal in (LabOrder.objects.filter(facility_id__in=self.facility_ids, test__category__name='Sickle cell testing',
                                                      status__in=['resulted', 'reviewed'], resulted_at__date__range=(start, end))
                              .exclude(test__code='scd-confirm').values_list('patient_id', 'is_abnormal')):
            tests += 1
            positive += bool(abnormal)
            patients.add(pid)
        return tests, positive, patients

    def echos(self, start, end):
        qs = InvestigationOrder.objects.filter(facility_id__in=self.facility_ids, modality='echo',
                                               status__in=['completed', 'reviewed'], performed_at__date__range=(start, end))
        rows = list(qs.values_list('patient_id', 'is_abnormal'))
        return len(rows), sum(1 for _p, a in rows if a), {p for p, _a in rows}, {p for p, a in rows if a}

    def hospitalized(self, start, end):
        return {pid for pid in self.patients if self.admitted(pid, start, end)
                or self.any_crf(pid, 'hospitalizations', end, since=start)}
