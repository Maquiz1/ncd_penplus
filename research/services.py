import datetime

from django.utils import timezone

from audit.models import AuditEvent
from audit.services import log_event

from .models import ConsentRecord, Study, StudyEligibilityAssessment, StudyEnrollment

MAIN_STUDY_CODE = 'penplus'  # everyone enrolled here is in this study; care-only patients are recorded in GoTHOMIS

# Research participation follows clinical care: the same outcome, on the same date.
PROGRAMME_TO_STUDY = {'active': 'active', 'pending': 'pending', 'ltfu': 'ltfu', 'transferred_out': 'transferred',
                      'withdrawn': 'withdrawn', 'deceased': 'deceased', 'completed': 'completed', 'other': 'other'}
REOPENABLE = ('ltfu', 'transferred', 'withdrawn', 'other')
STUDY_TO_PROGRAMME = {'withdrawn': 'withdrawn', 'ltfu': 'ltfu', 'transferred': 'transferred_out', 'deceased': 'deceased',
                      'completed': 'completed', 'other': 'other'}


def consent_withdrawn(enrollment):
    latest = ConsentRecord.objects.filter(patient=enrollment.patient_id, study=enrollment.study_id).order_by(
        '-consent_date', '-pk').first()
    return latest is not None and latest.decision == ConsentRecord.Decision.WITHDRAWN


def mirror_programme_status(programme_enrollment, user=None, request=None):
    """Apply a clinical programme status change to the patient's study enrollments.

    Closing care closes open study enrollments; returning to care reopens study enrollments that were closed
    the same way, unless consent for that study was withdrawn (re-enrolment then needs new consent).
    Returns (changed, skipped_for_consent).
    """
    target = PROGRAMME_TO_STUDY.get(programme_enrollment.status)
    if target is None:
        return [], []
    changed, skipped = [], []
    patient = programme_enrollment.patient
    # A subject still in care for another disease stays in the study.
    if target not in ('active', 'pending') and patient.programme_enrollments.filter(status__in=['pending', 'active']) \
            .exclude(pk=programme_enrollment.pk).exists():
        return [], []
    studies = StudyEnrollment.objects.filter(patient=patient).select_related('study')
    for enrollment in studies:
        if target in ('active', 'pending'):
            if enrollment.status not in REOPENABLE or enrollment.study.status == 'closed':
                continue
            if consent_withdrawn(enrollment):
                skipped.append(enrollment)
                continue
            outcome_date, reason = None, ''
        else:
            if enrollment.status not in ('active', 'pending'):
                continue
            outcome_date = programme_enrollment.outcome_date or timezone.localdate()
            reason = f'Follows clinical care: {programme_enrollment.outcome_reason or programme_enrollment.get_status_display()}'
        old = enrollment.status
        enrollment.status, enrollment.outcome_date, enrollment.outcome_reason = target, outcome_date, reason[:255]
        enrollment.save(update_fields=['status', 'outcome_date', 'outcome_reason', 'updated_at'])
        log_event(actor=user, request=request, action=AuditEvent.Action.STATUS, obj=enrollment,
                  changes={'status': [old, target]}, reason=f'Mirrored from {programme_enrollment.programme}')
        changed.append(enrollment)
    return changed, skipped


def research_consent_given(patient):
    """The pre-screening consent is the research consent; a later record (e.g. a withdrawal) overrides it."""
    latest = ConsentRecord.objects.filter(patient=patient, study__code=MAIN_STUDY_CODE).order_by('-consent_date', '-pk').first()
    if latest is not None:
        return latest.decision == ConsentRecord.Decision.CONSENTED
    pre = patient.prescreenings.order_by('-date', '-pk').first()
    return bool(pre and pre.consent_obtained)


def enroll_in_main_study(patient, facility, enrollment_date, request):
    """Study enrollment that goes with enrolling in care. Returns it, or None when the subject already has one open
    or the study is not set up."""
    study = Study.objects.filter(code=MAIN_STUDY_CODE).first()
    if study is None or patient.study_enrollments.filter(study=study, status__in=['pending', 'active']).exists():
        return None
    user = request.user
    earlier = patient.study_enrollments.filter(study=study).first()
    if earlier is not None:  # one participant ID per subject: coming back reopens the same study record
        old = earlier.status
        earlier.status, earlier.outcome_date, earlier.outcome_reason = StudyEnrollment.Status.ACTIVE, None, ''
        earlier.save(update_fields=['status', 'outcome_date', 'outcome_reason', 'updated_at'])
        log_event(request=request, action=AuditEvent.Action.STATUS, obj=earlier, changes={'status': [old, 'active']},
                  reason='Enrolled again')
        return earlier
    consent = patient.consents.filter(study=study, decision='consented').order_by('-consent_date', '-pk').first()
    if consent is None:
        pre = patient.prescreenings.order_by('-date', '-pk').first()
        day = pre.consent_date or pre.date
        consent = ConsentRecord.objects.create(
            patient=patient, study=study, consent_version=study.current_consent_version or '1.0', decision='consented',
            consent_date=timezone.make_aware(datetime.datetime.combine(day, datetime.time(9))), obtained_by=pre.created_by or user,
            notes='From the pre-screening consent.', created_by=user)
        log_event(request=request, action=AuditEvent.Action.CREATE, obj=consent)
    eligibility = patient.study_eligibility.filter(study=study, is_eligible=True).first()
    if eligibility is None:
        eligibility = StudyEligibilityAssessment.objects.create(
            patient=patient, study=study, is_eligible=True, assessed_by=user, assessed_on=enrollment_date,
            criteria_notes='Diagnosis confirmed by the doctor.', created_by=user)
    enrollment = StudyEnrollment.objects.create(
        patient=patient, study=study, participant_id=patient.patient_number, facility=facility, eligibility=eligibility,
        consent=consent, enrollment_date=enrollment_date, created_by=user)
    log_event(request=request, action=AuditEvent.Action.CREATE, obj=enrollment)
    return enrollment


def close_care_with_study(study_enrollment, request):
    """Leaving the study ends enrollment here: the subject's care continues in GoTHOMIS."""
    from clinical.models import EnrollmentEvent

    target = STUDY_TO_PROGRAMME.get(study_enrollment.status)
    if target is None:
        return []
    closed = []
    reason = f'Follows the study: {study_enrollment.outcome_reason or study_enrollment.get_status_display()}'[:255]
    day = study_enrollment.outcome_date or timezone.localdate()
    for enrollment in study_enrollment.patient.programme_enrollments.filter(status__in=['pending', 'active']):
        old = enrollment.status
        EnrollmentEvent.objects.create(enrollment=enrollment, from_status=old, to_status=target, event_date=day, reason=reason,
                                       created_by=request.user)
        enrollment.status, enrollment.outcome_date, enrollment.outcome_reason = target, day, reason
        enrollment.save(update_fields=['status', 'outcome_date', 'outcome_reason', 'updated_at'])
        log_event(request=request, action=AuditEvent.Action.STATUS, obj=enrollment, changes={'status': [old, target]},
                  reason=reason)
        closed.append(enrollment)
    return closed


def apply_consent_withdrawal(consent, request):
    """A withdrawn consent closes the study enrollment and, with it, enrollment in care here. Returns what was closed."""
    closed = []
    if consent.decision != ConsentRecord.Decision.WITHDRAWN:
        return closed
    for enrollment in StudyEnrollment.objects.filter(patient=consent.patient, study=consent.study, status__in=['pending', 'active']):
        enrollment.status = StudyEnrollment.Status.WITHDRAWN
        enrollment.outcome_date = consent.withdrawal_date
        enrollment.outcome_reason = (consent.withdrawal_reason or 'Consent withdrawn')[:255]
        enrollment.save(update_fields=['status', 'outcome_date', 'outcome_reason', 'updated_at'])
        log_event(request=request, action=AuditEvent.Action.STATUS, obj=enrollment, changes={'status': 'withdrawn'},
                  reason=enrollment.outcome_reason)
        closed += close_care_with_study(enrollment, request)
    return closed
