from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from audit.models import AuditEvent
from audit.services import log_event

from .models import Patient, PatientFacilityAssignment, PatientIdSequence, PatientTransfer


def next_patient_number(year):
    """Issue the next NPYY-NNNN number (NP = NCD PEN-Plus) using a row-locked per-year counter.

    The counter is never derived from a count of patients, so numbers are never
    reused after deletion or merge, and concurrent registrations cannot collide.
    """
    with transaction.atomic():
        try:
            with transaction.atomic():
                PatientIdSequence.objects.get_or_create(year=year)
        except IntegrityError:
            pass
        sequence = PatientIdSequence.objects.select_for_update().get(year=year)
        sequence.last_value += 1
        sequence.save(update_fields=['last_value'])
        return f'NP{year % 100:02d}-{sequence.last_value:04d}'


def find_possible_duplicates(first_name='', last_name='', date_of_birth=None, phone='', identifiers=()):
    """Return patients (including soft-deleted) that look like the same person."""
    condition = Q()
    if last_name and date_of_birth:
        condition |= Q(last_name__iexact=last_name, date_of_birth=date_of_birth)
    if first_name and last_name:
        condition |= Q(first_name__iexact=first_name, last_name__iexact=last_name)
    if phone:
        condition |= Q(phone=phone) | Q(alternate_phone=phone)
    for value in identifiers:
        if value:
            condition |= Q(identifiers__value__iexact=value)
    if not condition:
        return Patient.all_objects.none()
    return Patient.all_objects.filter(condition).distinct().select_related('current_facility')


@transaction.atomic
def register_patient(patient, user):
    patient.patient_number = next_patient_number(patient.registration_date.year)
    patient.current_facility = patient.registration_facility
    patient.created_by = user
    patient.save()
    PatientFacilityAssignment.objects.create(
        patient=patient, facility=patient.registration_facility, start_date=patient.registration_date,
        reason=PatientFacilityAssignment.Reason.REGISTRATION, created_by=user,
    )
    return patient


@transaction.atomic
def apply_transfer_action(transfer, action, user, request=None, notes=''):
    """Move a transfer through its workflow. Receiving updates the current assignment."""
    allowed_from, new_status, _side = PatientTransfer.TRANSITIONS[action]
    allowed_from = (allowed_from,) if isinstance(allowed_from, str) else allowed_from
    transfer = PatientTransfer.objects.select_for_update().get(pk=transfer.pk)
    if transfer.status not in allowed_from:
        raise ValueError(f'Cannot {action} a transfer that is {transfer.get_status_display().lower()}.')
    old_status = transfer.status
    transfer.status = new_status
    if notes:
        transfer.handover_notes = f'{transfer.handover_notes}\n[{action}] {notes}'.strip()
    if action in ('approve', 'reject'):
        transfer.decided_by = user
    if action == 'receive':
        patient = Patient.objects.select_for_update().get(pk=transfer.patient_id)
        today = timezone.localdate()
        transfer.received_by = user
        transfer.received_at = timezone.now()
        transfer.effective_date = transfer.effective_date or today
        PatientFacilityAssignment.objects.filter(patient=patient, is_current=True).update(
            is_current=False, end_date=transfer.effective_date)
        PatientFacilityAssignment.objects.create(
            patient=patient, facility=transfer.to_facility, start_date=transfer.effective_date,
            reason=PatientFacilityAssignment.Reason.TRANSFER, created_by=user)
        patient.current_facility = transfer.to_facility
        patient.save(update_fields=['current_facility', 'updated_at'])
    transfer.save()
    log_event(actor=user, request=request, action=AuditEvent.Action.TRANSFER, obj=transfer,
              changes={'status': [old_status, new_status]}, reason=notes)
    return transfer


@transaction.atomic
def record_death(patient, date_of_death, cause, user, request=None):
    """Death is a patient-level event: close active pathways and cancel future visits."""
    from clinical.models import ProgrammeEnrollment
    from pharmacy.models import Prescription
    from encounters.models import Appointment
    from research.models import StudyEnrollment

    patient.vital_status = Patient.VitalStatus.DECEASED
    patient.date_of_death = date_of_death
    patient.cause_of_death = cause
    patient.save(update_fields=['vital_status', 'date_of_death', 'cause_of_death', 'updated_at'])
    closed = {}
    for model in (ProgrammeEnrollment, StudyEnrollment):
        count = model.objects.filter(patient=patient, status__in=['pending', 'active']).update(
            status='deceased', outcome_date=date_of_death, outcome_reason='Subject died')
        closed[model._meta.model_name] = count
    closed['appointments_cancelled'] = Appointment.objects.filter(
        patient=patient, scheduled_date__gte=date_of_death,
        status__in=['scheduled', 'confirmed', 'rescheduled', 'expected'],
    ).update(status='cancelled', notes='Cancelled: subject deceased')
    closed['prescriptions_cancelled'] = Prescription.objects.filter(
        patient=patient, status__in=Prescription.OPEN_STATUSES,
    ).update(status='cancelled', cancel_reason='Subject deceased')
    log_event(actor=user, request=request, action=AuditEvent.Action.STATUS, obj=patient,
              changes={'vital_status': ['alive', 'deceased'], 'closed': closed}, reason=cause)
