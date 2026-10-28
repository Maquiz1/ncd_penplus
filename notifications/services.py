import datetime

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from encounters.models import SWAHILI_DAYS, Appointment

from .models import SmsMessage
from .sms import SmsError, get_backend, normalize_tz_phone, sms_config

MAX_ATTEMPTS = 3


def recipient_phone(patient):
    """The patient's own mobile, else the next of kin / guardian (e.g. children)."""
    for value, who in ((patient.phone, 'patient'), (patient.alternate_phone, 'patient'),
                       (patient.next_of_kin_phone, 'next of kin')):
        phone = normalize_tz_phone(value)
        if phone:
            return phone, who
    return '', ''


def sms_eligibility(patient, allow_without_consent=False):
    """(phone, who, reason-if-not-eligible)."""
    if patient.is_deceased or patient.is_deleted:
        return '', '', 'deceased or deleted'
    phone, who = recipient_phone(patient)
    if not phone:
        return '', '', 'no valid mobile number'
    if patient.sms_consent is False:
        return phone, who, 'declined SMS'
    if patient.sms_consent is None and not allow_without_consent:
        return phone, who, 'SMS consent not asked yet'
    return phone, who, ''


def queue_message(patient, purpose, body, *, appointment=None, clinic_session=None, for_date=None, user=None,
                  allow_without_consent=False):
    phone, who, reason = sms_eligibility(patient, allow_without_consent)
    if reason:
        return None, reason
    try:
        with transaction.atomic():
            message = SmsMessage.objects.create(patient=patient, appointment=appointment, clinic_session=clinic_session,
                                                purpose=purpose, phone=phone, recipient=who, body=body,
                                                for_date=for_date, created_by=user)
    except IntegrityError:
        return None, 'already queued'
    return message, ''


def still_relevant(message):
    """Do not send reminders for appointments that were cancelled, attended or moved to another date."""
    appointment = message.appointment
    if appointment is None:
        return True
    appointment.refresh_from_db()
    return appointment.is_open and (message.for_date is None or appointment.scheduled_date == message.for_date)


def process_queue(limit=200):
    """Send queued messages through the gateway. Retries failures up to MAX_ATTEMPTS. Returns counts."""
    backend = get_backend()
    counts = {'sent': 0, 'failed': 0, 'cancelled': 0}
    for message in SmsMessage.objects.filter(status__in=['queued', 'failed'], attempts__lt=MAX_ATTEMPTS).order_by('pk')[:limit]:
        if not still_relevant(message):
            message.status, message.error = SmsMessage.Status.CANCELLED, 'Appointment cancelled, attended or moved'
            message.save(update_fields=['status', 'error', 'updated_at'])
            counts['cancelled'] += 1
            continue
        message.attempts += 1
        message.backend = backend.name
        try:
            message.provider_reference = backend.send(message.phone, message.body)[:100]
            message.status, message.sent_at, message.error = SmsMessage.Status.SENT, timezone.now(), ''
            counts['sent'] += 1
        except SmsError as exc:
            message.status, message.error = SmsMessage.Status.FAILED, str(exc)[:255]
            counts['failed'] += 1
        message.save()
    return counts


def queue_reminders(today=None):
    """Queue the day-before and same-day reminders for open appointments (never twice for the same date)."""
    today = today or timezone.localdate()
    queued = 0
    for purpose, date, when in ((SmsMessage.Purpose.REMINDER_DAY_BEFORE, today + datetime.timedelta(days=1), 'kesho'),
                                (SmsMessage.Purpose.REMINDER_SAME_DAY, today, 'leo')):
        appointments = (Appointment.objects.filter(scheduled_date=date, status__in=['scheduled', 'confirmed', 'rescheduled'])
                        .select_related('patient', 'facility', 'clinic_session'))
        for appointment in appointments:
            session = appointment.clinic_session
            contact = (session.contact_phone if session else '') or sms_config().default_contact or appointment.facility.name
            body = settings.SMS_REMINDER_TEMPLATE.format(
                facility=appointment.facility.name, when=f'{when} ({SWAHILI_DAYS[date.weekday()]})',
                date=f'{date:%d/%m/%Y}', contact=contact)
            message, _reason = queue_message(appointment.patient, purpose, body, appointment=appointment,
                                             clinic_session=session, for_date=date,
                                             allow_without_consent=bool(session and session.send_without_consent))
            queued += message is not None
    return queued
