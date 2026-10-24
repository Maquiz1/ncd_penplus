from django.utils import timezone

from audit.models import AuditEvent
from audit.services import log_event

from .models import ConsentRecord, StudyEnrollment

# Research participation follows clinical care: the same outcome, on the same date.
PROGRAMME_TO_STUDY = {'active': 'active', 'pending': 'pending', 'ltfu': 'ltfu', 'transferred_out': 'transferred',
                      'withdrawn': 'withdrawn', 'deceased': 'deceased', 'completed': 'completed', 'other': 'other'}
REOPENABLE = ('ltfu', 'transferred', 'withdrawn', 'other')


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
    studies = StudyEnrollment.objects.filter(patient=programme_enrollment.patient).select_related('study')
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
