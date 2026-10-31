"""Data-quality checks. Each issue has a stable key, so running a check again never duplicates it
and never reopens an issue someone already resolved or dismissed."""
import datetime
from collections import defaultdict

from django.contrib.contenttypes.models import ContentType
from django.db.models import Count
from django.db.models.functions import Lower
from django.utils import timezone

from encounters.models import Encounter
from patients.models import Patient, PatientIdentifier
from research.models import ConsentRecord

from .models import DataIssue


def raise_issue(key, code, category, title, description='', patient=None, facility=None, obj=None,
                related_date=None):
    defaults = {'code': code, 'category': category, 'title': title[:200], 'description': description,
                'patient': patient, 'facility': facility or (patient.current_facility if patient else None),
                'related_date': related_date}
    if obj is not None:
        defaults.update(content_type=ContentType.objects.get_for_model(obj), object_id=str(obj.pk))
    return DataIssue.objects.get_or_create(key=key[:150], defaults=defaults)


MONTHLY_STAGES = ('baseline', 'follow_up')  # one per month is expected; any other visit that month is Unscheduled


def visits_same_month(patient_ids=None):
    """Two or more baseline/follow-up visits in one month: one is the monthly visit, the others should be Unscheduled.
    Closes by itself once at most one is left."""
    months = defaultdict(list)
    encounters = Encounter.objects.filter(stage__in=MONTHLY_STAGES).exclude(status='cancelled')
    if patient_ids is not None:
        encounters = encounters.filter(patient_id__in=patient_ids)
    for e in encounters.select_related('patient', 'appointment').order_by('encounter_date'):
        day = timezone.localtime(e.encounter_date)
        months[(e.patient_id, day.year, day.month)].append(e)
    created, still_needed = 0, set()
    for (patient_id, year, month), encounters in months.items():
        if len(encounters) < 2:
            continue
        first = datetime.date(year, month, 1)
        key = f'visits-month:{patient_id}:{year}-{month:02d}'
        still_needed.add(key)
        scheduled = sum(1 for e in encounters if e.appointment_id)
        _, new = raise_issue(
            key, 'visits_same_month', DataIssue.Category.VISITS, f'{len(encounters)} follow-up visits in {first:%B %Y}',
            f'Only one follow-up visit is expected per month; {scheduled} of these {len(encounters)} are linked to an '
            f'appointment. Keep the monthly visit and mark the others as Unscheduled.',
            patient=encounters[0].patient, related_date=first)
        created += new
    stale = DataIssue.objects.open().filter(code='visits_same_month').exclude(key__in=still_needed)
    if patient_ids is not None:
        stale = stale.filter(patient_id__in=patient_ids)
    stale.update(status=DataIssue.Status.RESOLVED, resolved_at=timezone.now(),
                 resolution_note='Only one follow-up visit left in the month (closed automatically).')
    return created


def duplicate_identifiers():
    created = 0
    groups = (PatientIdentifier.objects.annotate(v=Lower('value')).values('id_type', 'v')
              .annotate(n=Count('patient', distinct=True)).filter(n__gt=1))
    for group in groups:
        members = list(Patient.all_objects.filter(identifiers__id_type=group['id_type'],
                                                  identifiers__value__iexact=group['v']).distinct().order_by('pk'))
        label = dict(PatientIdentifier.IdType.choices).get(group['id_type'], group['id_type'])
        _, new = raise_issue(
            f'dup-id:{group["id_type"]}:{group["v"]}', 'duplicate_identifier', DataIssue.Category.IDENTITY,
            f'{label} "{group["v"]}" is shared by {len(members)} subjects',
            'Patients: ' + ', '.join(p.patient_number for p in members) +
            '. Correct the identifier on the wrong record, or soft-delete a true duplicate.',
            patient=members[0])
        created += new
    return created


def duplicate_people():
    created = 0
    groups = (Patient.all_objects.annotate(f=Lower('first_name'), l=Lower('last_name'))
              .values('f', 'l', 'date_of_birth').annotate(n=Count('id')).filter(n__gt=1))
    for group in groups:
        members = list(Patient.all_objects.filter(first_name__iexact=group['f'], last_name__iexact=group['l'],
                                                  date_of_birth=group['date_of_birth']).order_by('pk'))
        _, new = raise_issue(
            f'dup-person:{group["f"]}:{group["l"]}:{group["date_of_birth"]}', 'possible_duplicate_patient',
            DataIssue.Category.IDENTITY, f'{len(members)} subjects with the same name and date of birth',
            'Patients: ' + ', '.join(p.patient_number for p in members) + '. Check whether they are the same person.',
            patient=members[0])
        created += new
    return created


def missing_date_of_birth():
    created = 0
    for p in Patient.all_objects.filter(date_of_birth=datetime.date(1900, 1, 1), dob_estimated=True):
        _, new = raise_issue(f'dob:{p.pk}', 'missing_date_of_birth', DataIssue.Category.IDENTITY,
                             'Date of birth missing', 'Recorded as 1 Jan 1900 (unknown). Enter the real or estimated date.',
                             patient=p)
        created += new
    return created


def consent_to_verify():
    created = 0
    for c in ConsentRecord.objects.filter(notes__icontains='verify the paper consent').select_related('patient', 'study'):
        _, new = raise_issue(f'consent:{c.pk}', 'consent_to_verify', DataIssue.Category.CONSENT,
                             f'Consent for {c.study.code} not recorded in EDCS',
                             'The subject was enrolled in the EDCS without a consent entry. Check the signed paper '
                             'consent form and update the consent record.', patient=c.patient, obj=c,
                             related_date=c.consent_date.date())
        created += new
    return created


def address_problems():
    """Addresses that do not fit together, or patients in care without a ward."""
    created = 0
    patients = Patient.objects.select_related('residence_district', 'residence_ward', 'residence_street')
    for p in patients.filter(residence_ward__isnull=False):
        wrong = []
        if p.residence_district_id and p.residence_ward.district_id != p.residence_district_id:
            wrong.append(f'ward {p.residence_ward.name} is not in {p.residence_district.name} district')
        if p.residence_street_id and p.residence_street.ward_id != p.residence_ward_id:
            wrong.append(f'street/village {p.residence_street.name} is not in {p.residence_ward.name} ward')
        if wrong:
            _, new = raise_issue(f'address-mismatch:{p.pk}', 'address_mismatch', DataIssue.Category.LOCATIONS,
                                 'Address does not fit together', '; '.join(wrong).capitalize() + '. Correct the address.',
                                 patient=p)
            created += new
    in_care = patients.filter(residence_ward__isnull=True, vital_status='alive',
                              programme_enrollments__status__in=['active', 'pending', 'ltfu']).distinct()
    for p in in_care:
        _, new = raise_issue(f'address-incomplete:{p.pk}', 'address_incomplete', DataIssue.Category.LOCATIONS,
                             'Address incomplete (no ward)',
                             'Record the district, ward and street/village so the subject can be traced and counted '
                             'by area.', patient=p)
        created += new
    return created


# Results that should lead to confirming (or ruling out) the diagnosis of an enrolled patient.
DIAGNOSIS_EVIDENCE = {
    'cardiac': {'investigations': ('echo', 'ecg')},
    'diabetes': {'labs': ('hba1c', 'fbg', 'rbg')},
    'scd': {'labs': ('scd-result', 'scd-confirm', 'sickling', 'hb-electro')},
}
PROVISIONAL_DAYS = 60
DX_CODES = ('diagnosis_to_confirm_after_results', 'diagnosis_still_provisional')


def diagnosis_prompts(patient_ids=None):
    """Enrolled patients whose diagnosis is missing or still provisional:
    - results are available (echo/ECG, HbA1c/glucose, SCD tests) -> "confirm the diagnosis";
    - no results 60 days after enrolment -> "still provisional".
    Prompts close by themselves once a confirmed (or ruled-out) diagnosis is recorded."""
    from clinical.models import Diagnosis, ProgrammeEnrollment
    from investigations.models import InvestigationOrder
    from laboratory.models import LabOrder

    today = timezone.localdate()
    enrollments = ProgrammeEnrollment.objects.filter(status='active', programme__disease__code__in=DIAGNOSIS_EVIDENCE,
                                                     patient__is_deleted=False).select_related('patient', 'programme__disease')
    if patient_ids is not None:
        enrollments = enrollments.filter(patient_id__in=patient_ids)
    created, still_needed = 0, set()
    for enrollment in enrollments:
        patient, disease = enrollment.patient, enrollment.programme.disease
        diagnoses = Diagnosis.objects.filter(patient=patient, disease=disease)
        if diagnoses.filter(status__in=['confirmed', 'ruled_out']).exists():
            continue
        evidence = DIAGNOSIS_EVIDENCE[disease.code]
        results = []
        if 'investigations' in evidence:
            for inv in InvestigationOrder.objects.filter(patient=patient, modality__in=evidence['investigations'],
                                                         status__in=['completed', 'reviewed']).order_by('-performed_at')[:3]:
                flag = ' (abnormal)' if inv.is_abnormal else ' (normal)' if inv.is_abnormal is False else ''
                results.append(f'{inv.name} {inv.performed_at or inv.requested_at:%d %b %Y}{flag}'
                               + (f': {inv.conclusion}' if inv.conclusion else ''))
        if 'labs' in evidence:
            for lab in LabOrder.objects.filter(patient=patient, test__code__in=evidence['labs'],
                                               status__in=['resulted', 'reviewed']).select_related('test').order_by('-resulted_at')[:3]:
                results.append(f'{lab.test.name} {lab.resulted_at:%d %b %Y}: {lab.result_value} {lab.result_unit}'.strip())
        state = 'provisional' if diagnoses.exists() else 'not recorded'
        if results:
            code, key = DX_CODES[0], f'dx-results:{patient.pk}:{disease.code}'
            title = f'Results available: confirm the {disease.name.lower()} diagnosis'
            text = f'{disease.name} diagnosis is {state}. Latest results: ' + '; '.join(results) + \
                '. Record the confirmed condition / sub-type (or rule it out).'
        elif (today - enrollment.enrollment_date).days > PROVISIONAL_DAYS:
            code, key = DX_CODES[1], f'dx-provisional:{patient.pk}:{disease.code}'
            title = f'{disease.name} diagnosis still {state} {PROVISIONAL_DAYS}+ days after enrolment'
            text = f'Enrolled {enrollment.enrollment_date:%d %b %Y}. Order the confirming tests or record the diagnosis.'
        else:
            continue
        still_needed.add(key)
        issue, new = raise_issue(key, code, DataIssue.Category.CLINICAL, title, text, patient=patient,
                                 related_date=enrollment.enrollment_date)
        if not new and issue.status == DataIssue.Status.OPEN and issue.description != text:
            issue.description = text
            issue.save(update_fields=['description', 'updated_at'])
        created += new
    stale = DataIssue.objects.open().filter(code__in=DX_CODES).exclude(key__in=still_needed)
    if patient_ids is not None:
        stale = stale.filter(patient_id__in=patient_ids)
    stale.update(status=DataIssue.Status.RESOLVED, resolution_note='Diagnosis recorded (closed automatically).')
    return created


HBA1C_CODE = 'hba1c_overdue'


def hba1c_overdue(patient_ids=None):
    """Active diabetes patients without an HbA1c within the interval in Monitoring settings (or since enrolment).
    When monitoring is switched off, open flags are closed.
    One issue per due date, so the next cycle raises a new one; it closes by itself when a result arrives."""
    from clinical.models import ProgrammeEnrollment
    from laboratory.monitoring import active_rule, monitoring_status

    rule = active_rule('hba1c')
    enrollments = ProgrammeEnrollment.objects.filter(status='active', programme__disease__code=rule.disease.code if rule else None,
                                                     patient__is_deleted=False).select_related('patient')
    if patient_ids is not None:
        enrollments = enrollments.filter(patient_id__in=patient_ids)
    created, still_needed, seen = 0, set(), set()
    for enrollment in enrollments.order_by('enrollment_date'):
        patient = enrollment.patient
        if patient.pk in seen or patient.is_deceased:
            continue
        seen.add(patient.pk)
        status = monitoring_status(patient, enrollment_date=enrollment.enrollment_date)
        if not status['overdue']:
            continue
        key = f'hba1c-due:{patient.pk}:{status["due"]:%Y-%m-%d}'
        still_needed.add(key)
        if status['last']:
            text = (f'Last HbA1c {status["last"].result_value} {status["last"].result_unit} on '
                    f'{status["last_date"]:%d %b %Y} ({status["days"]} days ago); due {status["due"]:%d %b %Y}.').replace('  ', ' ')
        else:
            text = f'No HbA1c since enrolment ({enrollment.enrollment_date:%d %b %Y}); due {status["due"]:%d %b %Y}.'
        if status['gap']:
            gap = status['gap']
            text += f' Recorded as not done on {gap.recorded_on:%d %b %Y} ({gap.get_reason_display()}); still no result.'
        if status['pending']:
            text += f' Ordered {status["pending"].ordered_at:%d %b %Y}, waiting for the result.'
        else:
            text += ' Order an HbA1c at the next visit.'
        issue, new = raise_issue(key, HBA1C_CODE, DataIssue.Category.LAB, 'HbA1c overdue', text, patient=patient,
                                 related_date=status['due'])
        if not new and issue.status == DataIssue.Status.OPEN and issue.description != text:
            issue.description = text
            issue.save(update_fields=['description', 'updated_at'])
        created += new
    stale = DataIssue.objects.open().filter(code=HBA1C_CODE).exclude(key__in=still_needed)
    if patient_ids is not None:
        stale = stale.filter(patient_id__in=patient_ids)
    stale.update(status=DataIssue.Status.RESOLVED, resolution_note='HbA1c resulted or no longer due (closed automatically).')
    return created


DETECTORS = [visits_same_month, duplicate_identifiers, duplicate_people, missing_date_of_birth, consent_to_verify,
             address_problems, diagnosis_prompts, hba1c_overdue]


def run_all():
    return {detector.__name__: detector() for detector in DETECTORS}
