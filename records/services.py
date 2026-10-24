from django.db import transaction
from django.utils import timezone

from audit.models import AuditEvent
from audit.services import log_event


class RecordError(ValueError):
    pass


def _with_encounter(encounter):
    return f'Deleted with encounter {encounter.pk}'


def blockers(obj):
    """Reasons the record cannot be deleted yet (empty list = can delete)."""
    label = obj._meta.label
    if label == 'pharmacy.Prescription' and obj.dispensings.exists():
        return ['It has been dispensed; cancel the prescription instead.']
    if label == 'encounters.Encounter':
        reasons = []
        open_labs = obj.lab_orders.exclude(status='cancelled').count()
        open_inv = obj.investigations.exclude(status='cancelled').count()
        rx = obj.prescriptions.exclude(status='cancelled').count()
        for count, what in ((open_labs, 'lab test(s)'), (open_inv, 'investigation(s)'), (rx, 'prescription(s)')):
            if count:
                reasons.append(f'{count} {what} are linked to it: delete or cancel them first.')
        return reasons
    return []


def _reopen_appointment(appointment):
    """An appointment whose only encounter was deleted is no longer attended."""
    if appointment is None or appointment.status != 'attended' or appointment.encounters.exists():
        return
    appointment.status = 'missed' if appointment.scheduled_date < timezone.localdate() else 'scheduled'
    appointment.save(update_fields=['status', 'updated_at'])


@transaction.atomic
def delete_record(obj, user, reason, request=None):
    if not reason:
        raise RecordError('Give the reason for deleting this record.')
    problems = blockers(obj)
    if problems:
        raise RecordError(' '.join(problems))
    obj.soft_delete(user, reason[:255])
    cascaded = 0
    if obj._meta.label == 'encounters.Encounter':
        children = list(obj.vitals.all()) + list(obj.form_submissions.all())
        for child in children:
            child.soft_delete(user, f'{_with_encounter(obj)}: {reason}'[:255])
        cascaded = len(children)
        _reopen_appointment(obj.appointment)
    log_event(actor=user, request=request, action=AuditEvent.Action.DELETE, obj=obj, reason=reason,
              changes={'also_deleted': cascaded} if cascaded else None)
    return cascaded


@transaction.atomic
def restore_record(obj, user, request=None):
    if not user.is_superuser:
        raise RecordError('Only a superuser can restore deleted records.')
    obj.restore()
    restored = 0
    if obj._meta.label == 'encounters.Encounter':
        from crf.models import FormSubmission
        from encounters.models import Vitals
        prefix = _with_encounter(obj)
        for model in (Vitals, FormSubmission):
            for child in model.all_objects.filter(encounter=obj, is_deleted=True, deletion_reason__startswith=prefix):
                child.restore()
                restored += 1
        if obj.appointment_id and obj.appointment.status in ('missed', 'scheduled'):
            obj.appointment.status = 'attended'
            obj.appointment.save(update_fields=['status', 'updated_at'])
    log_event(actor=user, request=request, action=AuditEvent.Action.RESTORE, obj=obj,
              changes={'also_restored': restored} if restored else None)
    return restored
