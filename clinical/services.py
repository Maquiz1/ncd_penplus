from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from audit.models import AuditEvent
from audit.services import log_event
from core.utils import add_months

from .models import EnrollmentEvent, ProgrammeEnrollment


def mirror_programme_status(*args, **kwargs):
    from research.services import mirror_programme_status as mirror
    return mirror(*args, **kwargs)

LTFU_REASON = 'Default / lost to follow-up: no attended visit for {months} months after the last attended visit'


def last_attended(patient, since=None):
    """Date of the patient's last actual visit (any encounter that was not cancelled)."""
    qs = patient.encounters.exclude(status='cancelled')
    if since:
        qs = qs.filter(encounter_date__date__gte=since)
    value = qs.aggregate(last=Max('encounter_date'))['last']
    return timezone.localtime(value).date() if value else None


def ltfu_due_date(enrollment, months=None):
    """The date the enrollment becomes LTFU if the patient does not attend before it."""
    months = months or settings.LTFU_MONTHS
    returned = enrollment.events.filter(to_status='active').aggregate(d=Max('event_date'))['d']
    base = max(d for d in (enrollment.enrollment_date, returned, last_attended(enrollment.patient)) if d)
    return add_months(base, months)


def apply_ltfu_rule(user, today=None, months=None, dry_run=False, request=None):
    """Move active programme enrollments to LTFU when the patient has not attended for `months` months."""
    today = today or timezone.localdate()
    months = months or settings.LTFU_MONTHS
    changed = []
    for enrollment in ProgrammeEnrollment.objects.filter(status='active').select_related('patient', 'programme'):
        if enrollment.patient.is_deceased:
            continue
        due = ltfu_due_date(enrollment, months)
        if today <= due:
            continue
        changed.append((enrollment, due))
        if dry_run:
            continue
        with transaction.atomic():
            reason = LTFU_REASON.format(months=months)
            EnrollmentEvent.objects.create(enrollment=enrollment, from_status='active', to_status='ltfu',
                                           event_date=due, reason=reason, created_by=user)
            enrollment.status, enrollment.outcome_date, enrollment.outcome_reason = 'ltfu', due, reason
            enrollment.save(update_fields=['status', 'outcome_date', 'outcome_reason', 'updated_at'])
            log_event(actor=user, request=request, action=AuditEvent.Action.STATUS, obj=enrollment,
                      changes={'status': ['active', 'ltfu']}, reason=reason)
            mirror_programme_status(enrollment, user, request)
    return changed


def return_on_attendance(patient, date, user, request=None):
    """Under the default/LTFU rule, an attended visit by an LTFU patient is a return to care (care and research).

    Transfers out and withdrawals are not reopened automatically: staff use Return to care for those.
    """
    returned = []
    for enrollment in patient.programme_enrollments.filter(status='ltfu'):
        with transaction.atomic():
            EnrollmentEvent.objects.create(enrollment=enrollment, kind='return', from_status='ltfu', to_status='active',
                                           event_date=date, reason='Returned to care (attended visit)',
                                           created_by=user)
            enrollment.status, enrollment.outcome_date, enrollment.outcome_reason = 'active', None, ''
            enrollment.save(update_fields=['status', 'outcome_date', 'outcome_reason', 'updated_at'])
            log_event(actor=user, request=request, action=AuditEvent.Action.STATUS, obj=enrollment,
                      changes={'status': ['ltfu', 'active']}, reason='Returned to care (attended visit)')
            mirror_programme_status(enrollment, user, request)
        returned.append(enrollment)
    return returned
