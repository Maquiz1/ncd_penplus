"""Clinic days: schedule a group of patients for a specialist / PEN-Plus clinic and invite them by SMS."""
import calendar
import math
from collections import Counter, OrderedDict

from django.db import transaction
from django.utils import timezone

from audit.models import AuditEvent
from audit.services import log_event

from .models import Appointment, ClinicSession


def eligible_enrollments(session):
    from clinical.models import ProgrammeEnrollment

    statuses = ['active', 'pending'] + (['ltfu'] if session.include_ltfu else [])
    qs = ProgrammeEnrollment.objects.filter(status__in=statuses, patient__current_facility=session.facility,
                                            patient__is_deleted=False, patient__vital_status='alive')
    if session.programmes.exists():
        qs = qs.filter(programme__in=session.programmes.all())
    return qs.select_related('patient', 'programme').order_by('programme__name', 'patient__patient_number')


def eligible_patients(session):
    """{patient: first matching enrollment}, ordered by programme so the same disease is seen together."""
    patients = OrderedDict()
    for enrollment in eligible_enrollments(session):
        patients.setdefault(enrollment.patient, enrollment)
    return patients


def window(session):
    """Existing open appointments in the clinic's month(s) are replaced by the clinic day."""
    end = session.end_date or session.start_date
    return session.start_date.replace(day=1), end.replace(day=calendar.monthrange(end.year, end.month)[1])


def preview(session):
    from notifications.services import sms_eligibility

    patients = eligible_patients(session)
    first, last = window(session)
    by_programme = Counter(e.programme.name for e in patients.values())
    by_status = Counter(e.get_status_display() for e in patients.values())
    sms = Counter()
    existing = 0
    for patient in patients:
        _phone, _who, reason = sms_eligibility(patient, session.send_without_consent)
        sms[reason or 'will receive SMS'] += 1
        existing += patient.appointments.filter(status__in=Appointment.OPEN_STATUSES,
                                                scheduled_date__range=(first, last)).exists()
    return {'patients': len(patients), 'by_programme': dict(by_programme), 'by_status': dict(by_status),
            'sms': dict(sms), 'existing': existing, 'days': session.days(), 'window': (first, last)}


@transaction.atomic
def schedule(session, user, request=None):
    patients = list(eligible_patients(session).items())
    days = session.days()
    per_day = math.ceil(len(patients) / len(days)) if patients and session.allocation == 'spread' else len(patients) or 1
    first, last = window(session)
    counts = Counter()
    already = set(session.appointments.values_list('patient_id', flat=True))
    for index, (patient, enrollment) in enumerate(patients):
        if patient.pk in already:
            counts['already scheduled'] += 1
            continue
        day = days[min(index // per_day, len(days) - 1)]
        existing = (patient.appointments.filter(status__in=Appointment.OPEN_STATUSES, scheduled_date__range=(first, last))
                    .order_by('scheduled_date').first())
        if existing and not session.move_existing:
            counts['kept existing appointment'] += 1
            continue
        if existing:
            moved = existing.scheduled_date != day
            existing.scheduled_date, existing.scheduled_time = day, session.start_time
            existing.status = 'rescheduled' if moved else existing.status
            existing.reason_for_change = f'Moved to clinic day: {session.name}'[:255]
            existing.clinic_session = session
            existing.save()
            counts['existing appointment moved to clinic day'] += 1
        else:
            Appointment.objects.create(
                patient=patient, facility=session.facility, scheduled_date=day, scheduled_time=session.start_time,
                stage='follow_up', status='scheduled', programme_enrollment=enrollment, clinic_session=session,
                origin=Appointment.Origin.CLINIC_DAY, notes=session.name[:255], created_by=user)
            counts['new appointment'] += 1
    session.status = ClinicSession.Status.SCHEDULED
    session.save(update_fields=['status', 'updated_at'])
    log_event(actor=user, request=request, action=AuditEvent.Action.CREATE, obj=session, changes=dict(counts),
              reason='Clinic day appointments scheduled')
    return counts


def send_invitations(session, user, request=None):
    from notifications.models import SmsMessage
    from notifications.services import process_queue, queue_message

    reasons = Counter()
    for appointment in session.appointments.filter(status__in=Appointment.OPEN_STATUSES).select_related('patient'):
        message, reason = queue_message(appointment.patient, SmsMessage.Purpose.CLINIC_INVITE,
                                        session.render_sms(appointment.scheduled_date), appointment=appointment,
                                        clinic_session=session, for_date=appointment.scheduled_date, user=user,
                                        allow_without_consent=session.send_without_consent)
        reasons['queued' if message else reason] += 1
    sent = process_queue(limit=300)
    log_event(actor=user, request=request, action=AuditEvent.Action.CREATE, obj=session,
              changes={**reasons, **{f'sms {k}': v for k, v in sent.items()}}, reason='Clinic day SMS invitations')
    return reasons, sent


@transaction.atomic
def cancel(session, user, reason, request=None):
    from notifications.models import SmsMessage

    count = session.appointments.filter(status__in=Appointment.OPEN_STATUSES).update(
        status='cancelled', reason_for_change=f'Clinic day cancelled: {reason}'[:255])
    SmsMessage.objects.filter(clinic_session=session, status='queued').update(status='cancelled',
                                                                              error='Clinic day cancelled')
    session.status = ClinicSession.Status.CANCELLED
    session.save(update_fields=['status', 'updated_at'])
    log_event(actor=user, request=request, action=AuditEvent.Action.STATUS, obj=session,
              changes={'appointments_cancelled': count}, reason=reason)
    return count


def close_attendance(session, user, request=None):
    """After the clinic: appointments still open on past clinic days are marked missed."""
    count = session.appointments.filter(status__in=Appointment.OPEN_STATUSES,
                                        scheduled_date__lt=timezone.localdate()).update(status='missed')
    if session.end_date is None or session.end_date < timezone.localdate():
        session.status = ClinicSession.Status.COMPLETED
        session.save(update_fields=['status', 'updated_at'])
    log_event(actor=user, request=request, action=AuditEvent.Action.STATUS, obj=session,
              changes={'marked_missed': count}, reason='Clinic day attendance closed')
    return count
