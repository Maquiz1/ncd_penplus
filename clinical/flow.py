"""Where a patient is in the care pathway, and the patients who are stuck in it.

Per disease the path is: registered → pre-screened → screened → diagnosed → enrolled → baseline visit →
follow-up plan. Screening, diagnosis and enrollment happen once to bring the patient into care; visits and
appointments then repeat.
"""
from dataclasses import dataclass, field
from datetime import timedelta

from django.db.models import Exists, OuterRef
from django.urls import reverse
from django.utils import timezone

from .models import CLOSED_RETURNABLE, Diagnosis, PreScreening, ProgrammeEnrollment, Screening

PENDING_SCREENING_DAYS = 14
OPEN_ENROLLMENT = ('pending', 'active')


@dataclass
class Step:
    key: str
    label: str
    state: str = 'todo'  # done, skipped, waiting, stopped, todo
    note: str = ''
    date: object = None


@dataclass
class NextStep:
    label: str
    url: str
    perm: str
    hint: str = ''


@dataclass
class Track:
    disease: object
    steps: list = field(default_factory=list)
    next: NextStep = None
    message: str = ''
    in_care: bool = False


def _step(steps, key):
    return next(s for s in steps if s.key == key)


def patient_flow(patient):
    """One track per disease the patient was screened, diagnosed or enrolled for (or one general track)."""
    today = timezone.localdate()
    prescreenings = list(patient.prescreenings.order_by('-date', '-pk'))
    screenings = list(patient.screenings.select_related('disease').order_by('-screening_date', '-pk'))
    diagnoses = list(patient.diagnoses.select_related('disease').exclude(status=Diagnosis.Status.RULED_OUT)
                     .order_by('-diagnosis_date', '-pk'))
    ruled_out = set(patient.diagnoses.filter(status=Diagnosis.Status.RULED_OUT).values_list('disease_id', flat=True))
    enrollments = list(patient.programme_enrollments.select_related('programme__disease')
                       .order_by('-enrollment_date', '-pk'))
    encounters = patient.encounters.exclude(status='cancelled')
    baseline = encounters.filter(stage='baseline').order_by('encounter_date').first()
    follow_up = encounters.filter(stage__in=['follow_up', 'unscheduled']).order_by('encounter_date').first()
    plan = patient.followup_plans.filter(is_active=True).first()
    next_appointment = patient.appointments.filter(status__in=['scheduled', 'confirmed', 'rescheduled'],
                                                   scheduled_date__gte=today).order_by('scheduled_date').first()

    diseases = []
    for d in [e.programme.disease for e in enrollments] + [d.disease for d in diagnoses] + \
             [s.disease for s in screenings]:
        if d not in diseases:
            diseases.append(d)
    pk = patient.pk
    tracks = []
    for disease in diseases or [None]:
        d_screenings = [s for s in screenings if s.disease == disease]
        d_diagnoses = [d for d in diagnoses if d.disease == disease]
        d_enrollments = [e for e in enrollments if e.programme.disease == disease]
        open_enrollment = next((e for e in d_enrollments if e.status in OPEN_ENROLLMENT), None)
        arrived_diagnosed = bool(d_diagnoses or d_enrollments)
        track = Track(disease=disease, steps=[
            Step('registered', 'Registered', 'done', date=patient.registration_date),
            Step('prescreened', 'Pre-screened'), Step('screened', 'Screened'), Step('diagnosed', 'Diagnosed'),
            Step('enrolled', 'Enrolled'), Step('baseline', 'Baseline visit'), Step('plan', 'Follow-up plan')])
        steps = track.steps

        pre = _step(steps, 'prescreened')
        if prescreenings:
            latest = prescreenings[0]
            pre.date = latest.date
            if latest.disposition == PreScreening.Disposition.PROCEED or arrived_diagnosed or d_screenings:
                pre.state = 'done'
            else:
                pre.state, pre.note = 'stopped', latest.get_disposition_display()
        elif arrived_diagnosed:
            pre.state, pre.note = 'skipped', 'Not needed'

        scr = _step(steps, 'screened')
        decided = next((s for s in d_screenings if s.result != Screening.Result.PENDING), None)
        pending = next((s for s in d_screenings if s.result == Screening.Result.PENDING), None)
        if decided:
            scr.state, scr.date, scr.note = 'done', decided.screening_date, decided.get_result_display()
        elif pending:
            scr.state, scr.date, scr.note = 'waiting', pending.screening_date, 'Result pending'
        elif arrived_diagnosed:
            scr.state, scr.note = 'skipped', 'Arrived diagnosed'

        dx = _step(steps, 'diagnosed')
        if d_diagnoses:
            confirmed = next((d for d in d_diagnoses if d.status == Diagnosis.Status.CONFIRMED), None)
            shown = confirmed or d_diagnoses[0]
            dx.date = shown.diagnosis_date
            dx.state, dx.note = ('done', 'Confirmed') if confirmed else ('waiting', 'Provisional')
        elif disease and disease.pk in ruled_out:
            dx.state, dx.note = 'stopped', 'Ruled out'
        elif decided and decided.result != Screening.Result.POSITIVE and not d_enrollments:
            dx.state, dx.note = 'stopped', f'Screened {decided.get_result_display().lower()}'

        en = _step(steps, 'enrolled')
        if open_enrollment:
            en.state, en.date, en.note = 'done', open_enrollment.enrollment_date, str(open_enrollment.programme)
        elif d_enrollments:
            en.state, en.date, en.note = 'stopped', d_enrollments[0].outcome_date, d_enrollments[0].get_status_display()

        base = _step(steps, 'baseline')
        if baseline:
            base.state, base.date = 'done', timezone.localtime(baseline.encounter_date).date()
        elif follow_up:
            base.state, base.note = 'skipped', 'Follow-up visits recorded'

        pl = _step(steps, 'plan')
        if plan:
            pl.state, pl.date, pl.note = 'done', plan.anchor_date, plan.get_interval_months_display()
        elif next_appointment:
            pl.state, pl.note = 'done', 'Next appointment booked'
        if next_appointment:
            pl.date = next_appointment.scheduled_date

        _next_step(track, pk, pending, d_enrollments, disease, next_appointment)
        tracks.append(track)
    return tracks


def _next_step(track, pk, pending, enrollments, disease, next_appointment):
    steps = {s.key: s for s in track.steps}
    stopped = next((s for s in track.steps if s.state == 'stopped'), None)
    if stopped and stopped.key == 'enrolled':
        closed = enrollments[0]
        track.message = f'Programme closed: {closed.get_status_display()}.'
        if closed.status in CLOSED_RETURNABLE:
            track.next = NextStep('Return to care', reverse('clinical:enrollment_return', args=[closed.pk]),
                                  'clinical.change_programmeenrollment', 'If the subject is back in care.')
        return
    if stopped:
        track.message = {
            'prescreened': f'Pre-screening: {stopped.note.lower()}. No further steps unless the subject comes back.',
            'diagnosed': f'{stopped.note}. No diagnosis or enrollment needed.',
        }[stopped.key]
        return
    if steps['prescreened'].state == 'todo':
        track.next = NextStep('Record pre-screening', reverse('clinical:prescreening_add', args=[pk]),
                              'clinical.add_prescreening', 'Residence and expected stay, before disease screening.')
    elif steps['screened'].state == 'todo':
        track.next = NextStep('Record disease screening', reverse('clinical:screening_add', args=[pk]),
                              'clinical.add_screening')
    elif steps['screened'].state == 'waiting' and steps['diagnosed'].state == 'todo':
        track.next = NextStep('Record screening result', reverse('clinical:screening_update', args=[pending.pk]),
                              'clinical.change_screening',
                              f'{disease} screening of {pending.screening_date:%d %b %Y} is still pending.')
    elif steps['diagnosed'].state == 'todo':
        track.next = NextStep('Add diagnosis', reverse('clinical:diagnosis_add', args=[pk]), 'clinical.add_diagnosis',
                              'Screened positive. It can be provisional while results are pending.')
    elif steps['enrolled'].state == 'todo':
        track.next = NextStep('Enroll in programme', reverse('clinical:enrollment_add', args=[pk]),
                              'clinical.add_programmeenrollment', 'Enrolling puts the subject in the indicators.')
    elif steps['baseline'].state == 'todo':
        track.next = NextStep('Record baseline visit', reverse('encounters:encounter_add', args=[pk]) + '?stage=baseline',
                              'encounters.add_encounter', 'Today\'s visit, with no appointment.')
    elif steps['plan'].state == 'todo':
        track.next = NextStep('Create follow-up plan', reverse('encounters:plan_add', args=[pk]),
                              'encounters.add_followupplan', 'This books the future appointments.')
    else:
        track.in_care = True
        if next_appointment:
            track.message = f'In care. Next appointment {next_appointment.scheduled_date:%d %b %Y}.'
            if next_appointment.scheduled_date <= timezone.localdate():
                track.next = NextStep('Attend today\'s appointment',
                                      reverse('encounters:encounter_add', args=[pk]) + f'?appointment={next_appointment.pk}',
                                      'encounters.add_encounter')
        else:
            track.message = 'In care.'
    if steps['diagnosed'].state == 'waiting' and not track.message:
        track.message = 'Diagnosis is provisional: confirm it when results are back.'


# ----- patients stuck in the flow ---------------------------------------------------------------------------

GAPS = [
    ('screening_pending', 'Screening result pending', f'Disease screening still pending after {PENDING_SCREENING_DAYS} days.'),
    ('positive_no_diagnosis', 'Positive, no diagnosis', 'Screened positive but no diagnosis recorded for that disease.'),
    ('diagnosed_not_enrolled', 'Diagnosed, not enrolled', 'A diagnosis but never enrolled in that disease\'s programme.'),
    ('enrolled_no_baseline', 'Enrolled, no baseline visit', 'Active enrollment but no baseline or follow-up visit.'),
    ('no_plan', 'No follow-up plan', 'Active enrollment with no follow-up plan and no upcoming appointment.'),
]


def gap_querysets(patients):
    """Patient querysets per gap, limited to the given patients (already scoped to the user)."""
    from encounters.models import Encounter, FollowUpPlan, Appointment

    today = timezone.localdate()
    patient = OuterRef('patient_id')
    disease = OuterRef('disease_id')
    has_dx = Diagnosis.objects.filter(patient=patient, disease=disease)
    ever_enrolled = ProgrammeEnrollment.objects.filter(patient=patient, programme__disease=disease)

    pending = Screening.objects.filter(result=Screening.Result.PENDING,
                                       screening_date__lte=today - timedelta(days=PENDING_SCREENING_DAYS)) \
        .exclude(Exists(has_dx)).exclude(Exists(ever_enrolled))
    positive = Screening.objects.filter(result=Screening.Result.POSITIVE).exclude(Exists(has_dx)) \
        .exclude(Exists(ever_enrolled))
    not_enrolled = Diagnosis.objects.exclude(status=Diagnosis.Status.RULED_OUT).exclude(Exists(ever_enrolled))

    active = ProgrammeEnrollment.objects.filter(status__in=OPEN_ENROLLMENT).values('patient_id')
    visits = Encounter.objects.filter(patient=OuterRef('pk'), stage__in=['baseline', 'follow_up', 'unscheduled']) \
        .exclude(status='cancelled')
    plans = FollowUpPlan.objects.filter(patient=OuterRef('pk'), is_active=True)
    upcoming = Appointment.objects.filter(patient=OuterRef('pk'), status__in=['scheduled', 'confirmed', 'rescheduled'],
                                          scheduled_date__gte=today)
    enrolled = patients.filter(pk__in=active)
    return {
        'screening_pending': patients.filter(pk__in=pending.values('patient_id')),
        'positive_no_diagnosis': patients.filter(pk__in=positive.values('patient_id')),
        'diagnosed_not_enrolled': patients.filter(pk__in=not_enrolled.values('patient_id')),
        'enrolled_no_baseline': enrolled.exclude(Exists(visits)),
        'no_plan': enrolled.filter(Exists(visits)).exclude(Exists(plans)).exclude(Exists(upcoming)),
    }


def gap_details(key, patient_ids):
    """Per patient, what the gap is about (diseases, dates) for display."""
    rows = {}
    if key in ('screening_pending', 'positive_no_diagnosis'):
        result = 'pending' if key == 'screening_pending' else 'positive'
        for s in Screening.objects.filter(patient_id__in=patient_ids, result=result).select_related('disease'):
            rows.setdefault(s.patient_id, []).append(f'{s.disease} screening {s.screening_date:%d %b %Y}')
    elif key == 'diagnosed_not_enrolled':
        for d in Diagnosis.objects.filter(patient_id__in=patient_ids).exclude(status='ruled_out') \
                .select_related('disease'):
            rows.setdefault(d.patient_id, []).append(f'{d.description} ({d.get_status_display().lower()}, '
                                                     f'{d.diagnosis_date:%d %b %Y})')
    else:
        for e in ProgrammeEnrollment.objects.filter(patient_id__in=patient_ids, status__in=OPEN_ENROLLMENT) \
                .select_related('programme'):
            rows.setdefault(e.patient_id, []).append(f'{e.programme} since {e.enrollment_date:%d %b %Y}')
    return rows
