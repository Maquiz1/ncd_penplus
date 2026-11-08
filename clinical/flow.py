"""Where a patient is in the care pathway, and the patients who are stuck in it.

Per disease the path is: registered → pre-screened → screened (what to screen for; optional with a known NCD) →
investigations → diagnosis → enrolled → follow-up plan. All of that is one baseline visit; follow-up and
unscheduled visits then repeat.
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
    also: list = field(default_factory=list)
    message: str = ''
    in_care: bool = False


def _step(steps, key):
    return next(s for s in steps if s.key == key)


def _orders(patient):
    """(date, still waiting for a result) of every laboratory and radiology request."""
    rows = [(timezone.localtime(when).date(), status in ('ordered', 'collected'))
            for when, status in patient.lab_orders.exclude(status='cancelled').values_list('ordered_at', 'status')]
    rows += [(timezone.localtime(when).date(), status in ('requested', 'scheduled'))
             for when, status in patient.investigations.exclude(status='cancelled').values_list('requested_at', 'status')]
    return rows


def patient_flow(patient):
    """One track per disease the patient was screened, diagnosed or enrolled for (or one general track)."""
    today = timezone.localdate()
    prescreenings = list(patient.prescreenings.order_by('-date', '-pk'))
    known_ncd = bool(prescreenings and prescreenings[0].known_ncd)
    known_diseases = list(prescreenings[0].known_diseases.all()) if known_ncd else []
    screenings = list(patient.screenings.select_related('disease').order_by('-screening_date', '-pk'))
    diagnoses = list(patient.diagnoses.select_related('disease').exclude(status=Diagnosis.Status.RULED_OUT)
                     .order_by('-diagnosis_date', '-pk'))
    ruled_out = set(patient.diagnoses.filter(status=Diagnosis.Status.RULED_OUT).values_list('disease_id', flat=True))
    enrollments = list(patient.programme_enrollments.select_related('programme__disease')
                       .order_by('-enrollment_date', '-pk'))
    orders = _orders(patient)
    plans = list(patient.followup_plans.filter(is_active=True).prefetch_related('programmes'))
    next_appointment = patient.appointments.filter(status__in=['scheduled', 'confirmed', 'rescheduled'],
                                                   scheduled_date__gte=today).order_by('scheduled_date').first()

    diseases = []
    for d in [e.programme.disease for e in enrollments] + [d.disease for d in diagnoses] + \
             [s.disease for s in screenings] + known_diseases:
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
            Step('prescreened', 'Consent'), Step('screened', 'Screening'), Step('investigations', 'Investigations'),
            Step('diagnosed', 'Diagnosed'),
            Step('enrolled', 'Enrolled'), Step('plan', 'Follow-up plan')])
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
        # The latest screening counts: a re-screening reopens the path after an earlier negative one.
        latest_screening = d_screenings[0] if d_screenings else None
        pending = latest_screening if latest_screening and latest_screening.result == Screening.Result.PENDING else None
        decided = None if pending else latest_screening
        if decided:
            scr.state, scr.date, scr.note = 'done', decided.screening_date, decided.get_result_display()
        elif pending:
            scr.state, scr.date = 'done', pending.screening_date
            if not any(d.status == Diagnosis.Status.CONFIRMED for d in d_diagnoses):
                scr.note = 'Re-screening: awaiting doctor' if pending.rescreen_of_id else 'Awaiting doctor'
        elif known_ncd:
            scr.state, scr.note = 'skipped', 'Optional: known NCD'
        elif arrived_diagnosed:
            scr.state, scr.note = 'skipped', 'Arrived diagnosed'

        inv = _step(steps, 'investigations')
        since = latest_screening.screening_date if latest_screening else None
        relevant = [o for o in orders if since is None or o[0] >= since]
        confirmed_or_enrolled = d_enrollments or any(d.status == Diagnosis.Status.CONFIRMED for d in d_diagnoses)
        if any(waiting for _when, waiting in relevant) and not confirmed_or_enrolled:
            inv.state, inv.note = 'waiting', 'Results pending'
        elif relevant:
            inv.state, inv.date = 'done', max(when for when, _waiting in relevant)
        elif arrived_diagnosed or (decided and decided.result != Screening.Result.POSITIVE):
            inv.state, inv.note = 'skipped', 'None requested'
        elif prescreenings and prescreenings[0].lab_test_requested is False:
            inv.state, inv.note = 'skipped', 'Not requested'

        dx = _step(steps, 'diagnosed')
        if d_diagnoses:
            confirmed = next((d for d in d_diagnoses if d.status == Diagnosis.Status.CONFIRMED), None)
            shown = confirmed or d_diagnoses[0]
            dx.date = shown.diagnosis_date
            dx.state, dx.note = ('done', 'Confirmed') if confirmed else ('waiting', 'Suspected')
        elif disease and disease.pk in ruled_out:
            dx.state, dx.note = 'stopped', 'Ruled out'
        elif decided and decided.result != Screening.Result.POSITIVE and not d_enrollments:
            dx.state, dx.note = 'stopped', ('Not diagnosed' if decided.result == Screening.Result.NEGATIVE
                                            else f'Screened {decided.get_result_display().lower()}')

        en = _step(steps, 'enrolled')
        if open_enrollment:
            en.state, en.date, en.note = 'done', open_enrollment.enrollment_date, str(open_enrollment.programme)
        elif d_enrollments:
            en.state, en.date, en.note = 'stopped', d_enrollments[0].outcome_date, d_enrollments[0].get_status_display()

        pl = _step(steps, 'plan')
        # The schedule of this disease's programme (each programme follows its own).
        plan = next((p for p in plans if open_enrollment in p.programmes.all()
                     or p.programme_enrollment_id == open_enrollment.pk), None) if open_enrollment else None
        if plan:
            pl.state, pl.date, pl.note = 'done', plan.anchor_date, plan.get_interval_months_display()
        elif next_appointment:
            pl.state, pl.note = 'done', 'Next appointment booked'
        if next_appointment:
            pl.date = next_appointment.scheduled_date

        provisional = next((d for d in d_diagnoses if d.status == Diagnosis.Status.PROVISIONAL), None)
        _next_step(track, pk, pending, d_enrollments, disease, next_appointment, provisional, known_ncd)
        tracks.append(track)
    return tracks


def _next_step(track, pk, pending, enrollments, disease, next_appointment, provisional, known_ncd):
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
            'prescreened': f'Screening: {stopped.note.lower()}. No further steps unless the subject comes back.',
            'diagnosed': f'{stopped.note}. No enrollment needed.',
        }[stopped.key]
        return
    if steps['prescreened'].state == 'todo':
        track.next = NextStep('Record screening', reverse('clinical:prescreening_add', args=[pk]),
                              'clinical.add_prescreening', 'Consent, residence, known NCD and what to screen for.')
    elif steps['screened'].state == 'todo':
        track.next = NextStep('Choose what to screen for', reverse('clinical:prescreening_add', args=[pk]),
                              'clinical.add_prescreening', 'In the screening form: request investigations, then tick what to screen for.')
    elif steps['diagnosed'].state == 'todo':
        confirm = NextStep('Diagnosis',
                           reverse('clinical:diagnosis_add', args=[pk]) + (f'?disease={disease.pk}' if disease else ''),
                           'clinical.add_diagnosis')
        request = NextStep('Request investigations', reverse('investigations:request', args=[pk]),
                           'laboratory.add_laborder')
        if steps['investigations'].state == 'todo':
            track.next, track.also = request, [confirm]
            request.hint = 'Then the doctor confirms the diagnosis from the results.'
            if known_ncd and steps['screened'].state == 'skipped':
                track.also.append(NextStep('Screening (optional)', reverse('clinical:prescreening_add', args=[pk]),
                                           'clinical.add_prescreening'))
        elif steps['investigations'].state == 'waiting':
            track.message = 'Waiting for results, then the doctor confirms the diagnosis.'
            track.also = [request, confirm]
        else:
            confirm.hint = 'The doctor decides from the results.'
            track.next = confirm
        if pending:
            track.also.append(NextStep('Not diagnosed', reverse('clinical:screening_not_diagnosed', args=[pending.pk]),
                                       'clinical.add_diagnosis'))
    elif steps['enrolled'].state == 'todo' and steps['diagnosed'].state == 'waiting' and provisional:
        track.next = NextStep('Confirm diagnosis', reverse('clinical:diagnosis_update', args=[provisional.pk]),
                              'clinical.change_diagnosis', 'Enrolling needs a diagnosis confirmed by the doctor.')
    elif steps['enrolled'].state == 'todo':
        track.next = NextStep('Enroll in programme', reverse('clinical:enrollment_add', args=[pk]),
                              'clinical.add_programmeenrollment', 'Every disease the doctor confirmed can be enrolled together.')
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


# ----- the Baseline, Follow-up and Unscheduled panels of the subject page ------------------------------------

@dataclass
class ActionStep:
    label: str
    url: str
    perm: str
    done: bool = False
    note: str = ''
    disabled: bool = False


def visit_steps(visit):
    """What is done inside one visit, each step linked directly to it."""
    from crf.services import encounter_form_status

    patient, query = visit.patient, f'?encounter={visit.pk}'
    rows = encounter_form_status(visit)
    # Vital signs count as one of the visit's forms.
    missing = sum(1 for r in rows if r['required'] and not r['submissions']) + (not visit.vitals.exists())
    # Counted for this visit only: its laboratory tests and its imaging / ECG / echo requests.
    lab, imaging = visit.lab_orders.exclude(status='cancelled').count(), visit.investigations.exclude(status='cancelled').count()
    orders = lab + imaging
    counted = ' · '.join(part for part in (f'{lab} lab' if lab else '', f'{imaging} imaging' if imaging else '') if part)
    # An unscheduled visit may need no tests or medicines.
    optional = 'Optional' if visit.stage == 'unscheduled' else ''
    steps = [
        ActionStep('Forms', visit.get_absolute_url() + '#visit-forms', 'encounters.view_encounter', not missing,
                   f'{missing} missing' if missing else ''),
        ActionStep('Investigations', reverse('investigations:request', args=[patient.pk]) + query,
                   'laboratory.add_laborder', orders > 0, counted if orders else optional),
    ]
    if not patient.is_deceased:
        prescribed = visit.prescriptions.exists()
        steps.append(ActionStep('Prescribe', reverse('pharmacy:prescription_add', args=[patient.pk]) + query,
                                'pharmacy.add_prescription', prescribed, '' if prescribed else optional))
    # Admitted from this visit; while the stay is open the step leads to the discharge.
    stay = visit.admissions.order_by('-admitted_at').first()
    if stay and not stay.discharged_at:
        steps.append(ActionStep('Discharge', reverse('encounters:admission_discharge', args=[stay.pk]),
                                'encounters.change_admission', False, f'admitted to {stay.ward}'))
    elif stay or not patient.is_deceased:
        steps.append(ActionStep('Admission', reverse('encounters:admission_add', args=[patient.pk]) + query,
                                'encounters.add_admission', stay is not None, 'Optional' if stay is None else ''))
    steps.append(ActionStep('Close visit', reverse('encounters:encounter_update', args=[visit.pk]),
                            'encounters.change_encounter', visit.status == 'completed'))
    return steps


def investigations(patient, query):
    """Greyed out when the screening form says no investigations were requested: the doctor confirms directly."""
    ordered = patient.lab_orders.exists() or patient.investigations.exists()
    pre = patient.prescreenings.order_by('-date', '-pk').first()
    skipped = not ordered and pre is not None and pre.lab_test_requested is False
    return ActionStep('Investigations', reverse('investigations:request', args=[patient.pk]) + query,
                      'laboratory.add_laborder', ordered, 'Not requested' if skipped else '', disabled=skipped)


def _baseline_steps(patient, visit):
    pk, query = patient.pk, f'?encounter={visit.pk}' if visit else ''
    add = lambda name: reverse(name, args=[pk])
    pre = patient.prescreenings.order_by('-date', '-pk').first()
    steps = [
        ActionStep('Screening', add('clinical:prescreening_add'), 'clinical.add_prescreening', pre is not None),
        investigations(patient, query),
        ActionStep('Diagnosis', add('clinical:diagnosis_add') + query, 'clinical.add_diagnosis',
                   patient.diagnoses.filter(status=Diagnosis.Status.CONFIRMED).exists()),
    ]
    # Treatment starts once the doctor confirms, and before enrolling closes the baseline visit. Optional.
    if not patient.is_deceased:
        prescribed = (visit.prescriptions if visit else patient.prescriptions).exists()
        steps.append(ActionStep('Prescribe', reverse('pharmacy:prescription_add', args=[pk]) + query,
                                'pharmacy.add_prescription', prescribed, '' if prescribed else 'Optional'))
    # A subject can be admitted during the baseline visit (for example in DKA). Optional.
    stay = visit.admissions.order_by('-admitted_at').first() if visit else None
    if stay and not stay.discharged_at:
        steps.append(ActionStep('Discharge', reverse('encounters:admission_discharge', args=[stay.pk]),
                                'encounters.change_admission', False, f'admitted to {stay.ward}'))
    elif stay or not patient.is_deceased:
        steps.append(ActionStep('Admission', add('encounters:admission_add') + query, 'encounters.add_admission',
                                stay is not None, 'Optional' if stay is None else ''))
    steps += [
        ActionStep('Enroll', add('clinical:enrollment_add'), 'clinical.add_programmeenrollment',
                   patient.programme_enrollments.exists()),
    ]
    # Forms and the follow-up plan are tabs below the steps.
    return steps


def _visit_rows(visits):
    """Each visit with how many of its required forms (vital signs included) are still missing."""
    from crf.services import encounter_form_status

    rows = []
    for visit in visits:
        missing = sum(1 for r in encounter_form_status(visit) if r['required'] and not r['submissions'])
        rows.append({'visit': visit, 'missing': missing + (not visit.vitals.exists())})
    return rows


def patient_pathway(patient):
    """Everything the Baseline, Follow-up and Unscheduled panels show."""
    visits = patient.encounters.exclude(status='cancelled').order_by('-encounter_date')
    enrolled = patient.programme_enrollments.exists()
    baseline = visits.filter(stage='baseline').first()
    follow_up = visits.filter(stage='follow_up', status__in=['open', 'in_progress']).first()
    unscheduled = visits.filter(stage='unscheduled', status__in=['open', 'in_progress']).first()
    # A subject who comes late still attends the booked appointment: the latest one missed, otherwise the next.
    today = timezone.localdate()
    booked = patient.appointments.filter(status__in=['scheduled', 'confirmed', 'rescheduled'])
    appointment = (booked.filter(scheduled_date__lt=today).order_by('-scheduled_date').first()
                   or booked.filter(scheduled_date__gte=today).order_by('scheduled_date').first())
    start = reverse('encounters:encounter_add', args=[patient.pk])
    return {
        'active': ('unscheduled' if unscheduled else
                   'follow_up' if follow_up or (enrolled and (appointment or patient.followup_plans.filter(is_active=True).exists()))
                   else 'baseline'),
        'enrolled': enrolled,
        'baseline': baseline,
        'baseline_steps': _baseline_steps(patient, baseline),
        # The panels list visits; the work (forms, investigations, prescribing) is done inside each visit.
        'follow_up': follow_up,
        'follow_up_visits': _visit_rows(visits.filter(stage='follow_up').select_related('appointment', 'facility')[:10]),
        'unscheduled': unscheduled,
        'unscheduled_visits': _visit_rows(visits.filter(stage='unscheduled').select_related('facility')[:10]),
        # A visit left open on an earlier day must not stop today's visit from starting.
        'follow_up_stale': bool(follow_up and timezone.localdate(follow_up.encounter_date) < today),
        'unscheduled_stale': bool(unscheduled and timezone.localdate(unscheduled.encounter_date) < today),
        'can_start_follow_up': not follow_up or timezone.localdate(follow_up.encounter_date) < today,
        'can_start_unscheduled': not unscheduled or timezone.localdate(unscheduled.encounter_date) < today,
        'upcoming': booked.filter(scheduled_date__gte=today).exclude(pk=getattr(appointment, 'pk', None))
                          .order_by('scheduled_date')[:3],
        'plan': patient.followup_plans.filter(is_active=True).first(),
        'appointment': appointment,
        'appointment_overdue': bool(appointment and appointment.scheduled_date < today),
        'attend_url': f'{start}?appointment={appointment.pk}' if appointment else '',
        'start_follow_up_url': f'{start}?stage=follow_up',
        'start_unscheduled_url': f'{start}?stage=unscheduled',
    }


# ----- patients stuck in the flow ---------------------------------------------------------------------------

GAPS = [
    ('screening_pending', 'Waiting for doctor decision', f'Screened over {PENDING_SCREENING_DAYS} days ago, with no decision from the doctor.'),
    ('positive_no_diagnosis', 'Positive, no diagnosis', 'Screened positive but no diagnosis recorded for that disease.'),
    ('diagnosed_not_enrolled', 'Diagnosed, not enrolled', 'A diagnosis but never enrolled in that disease\'s programme.'),
    ('enrolled_no_baseline', 'Enrolled, no baseline visit', 'Active enrollment but no baseline or follow-up visit.'),
    ('no_plan', 'No follow-up plan', 'A programme with no follow-up schedule, and no upcoming appointment.'),
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
    # An open programme enrollment that no active schedule covers.
    active_plans = FollowUpPlan.objects.filter(is_active=True)
    unscheduled_programmes = ProgrammeEnrollment.objects.filter(status__in=OPEN_ENROLLMENT).exclude(
        pk__in=active_plans.filter(programmes__isnull=False).values('programmes')).exclude(
        pk__in=active_plans.filter(programme_enrollment__isnull=False).values('programme_enrollment'))  # no NULLs in NOT IN
    upcoming = Appointment.objects.filter(patient=OuterRef('pk'), status__in=['scheduled', 'confirmed', 'rescheduled'],
                                          scheduled_date__gte=today)
    enrolled = patients.filter(pk__in=active)
    return {
        'screening_pending': patients.filter(pk__in=pending.values('patient_id')),
        'positive_no_diagnosis': patients.filter(pk__in=positive.values('patient_id')),
        'diagnosed_not_enrolled': patients.filter(pk__in=not_enrolled.values('patient_id')),
        'enrolled_no_baseline': enrolled.exclude(Exists(visits)),
        'no_plan': enrolled.filter(Exists(visits), pk__in=unscheduled_programmes.values('patient_id'))
                           .exclude(Exists(upcoming)),
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
