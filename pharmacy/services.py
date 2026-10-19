from django.db import transaction
from django.db.models import Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from audit.models import AuditEvent
from audit.services import log_event

from .models import Dispensing, Medication, Prescription, StockBatch, StockMovement


class StockError(ValueError):
    pass


def _move(batch, movement_type, delta, user, reason='', dispensing=None, request=None):
    """Change a batch quantity under a row lock and write the ledger entry. Stock never goes negative."""
    batch = StockBatch.objects.select_for_update().get(pk=batch.pk)
    balance = batch.quantity_on_hand + delta
    if balance < 0:
        raise StockError(f'Only {batch.quantity_on_hand} {batch.medication.dispensing_unit}(s) left in batch '
                         f'{batch.batch_number}.')
    batch.quantity_on_hand = balance
    batch.save(update_fields=['quantity_on_hand', 'updated_at'])
    movement = StockMovement.objects.create(batch=batch, movement_type=movement_type, quantity=delta,
                                            balance_after=balance, reason=reason[:255], dispensing=dispensing,
                                            performed_by=user)
    log_event(actor=user, request=request, action=AuditEvent.Action.STATUS, obj=batch, reason=reason,
              changes={'movement': movement_type, 'quantity': delta, 'balance_after': balance})
    return movement


@transaction.atomic
def receive_stock(medication, facility, batch_number, expiry_date, quantity, user, supplier='', received_date=None,
                  request=None):
    if quantity <= 0:
        raise StockError('Quantity received must be greater than zero.')
    batch, created = StockBatch.objects.get_or_create(
        medication=medication, facility=facility, batch_number=batch_number,
        defaults={'expiry_date': expiry_date, 'supplier': supplier, 'created_by': user,
                  'received_date': received_date or timezone.localdate()})
    if not created and batch.expiry_date != expiry_date:
        raise StockError(f'Batch {batch_number} already exists here with expiry {batch.expiry_date:%d %b %Y}.')
    _move(batch, StockMovement.MovementType.RECEIPT, quantity, user,
          reason=f'Received from {supplier}' if supplier else 'Received', request=request)
    batch.refresh_from_db()
    return batch


@transaction.atomic
def adjust_stock(batch, delta, reason, user, write_off=False, request=None):
    if not reason:
        raise StockError('A reason is required for every stock adjustment.')
    if delta == 0:
        raise StockError('Adjustment quantity cannot be zero.')
    if write_off and delta > 0:
        raise StockError('A write-off removes stock.')
    kind = StockMovement.MovementType.WRITE_OFF if write_off else StockMovement.MovementType.ADJUSTMENT
    return _move(batch, kind, delta, user, reason=reason, request=request)


def available_batches(medication, facility):
    """Dispensable batches, first-expiry-first-out."""
    return StockBatch.objects.filter(medication=medication, facility=facility, quantity_on_hand__gt=0,
                                     expiry_date__gte=timezone.localdate()).order_by('expiry_date', 'pk')


@transaction.atomic
def dispense(prescription, batch, quantity, user, notes='', request=None):
    prescription = Prescription.objects.select_for_update().get(pk=prescription.pk)
    if not prescription.is_open:
        raise StockError(f'This prescription is {prescription.get_status_display().lower()}.')
    if batch.medication_id != prescription.medication_id:
        raise StockError('The batch is for a different medication.')
    if batch.facility_id != prescription.facility_id:
        raise StockError('The batch is held at a different facility.')
    if batch.is_expired:
        raise StockError(f'Batch {batch.batch_number} expired on {batch.expiry_date:%d %b %Y} and cannot be dispensed.')
    if quantity <= 0:
        raise StockError('Quantity must be greater than zero.')
    if quantity > prescription.quantity_remaining:
        raise StockError(f'Only {prescription.quantity_remaining} remain to be dispensed on this prescription.')
    dispensing = Dispensing.objects.create(prescription=prescription, batch=batch, quantity=quantity,
                                           dispensed_by=user, counselling_notes=notes, created_by=user)
    _move(batch, StockMovement.MovementType.DISPENSE, -quantity, user, dispensing=dispensing,
          reason=f'Dispensed to {prescription.patient}', request=request)
    old = prescription.status
    prescription.status = (Prescription.Status.DISPENSED if prescription.quantity_remaining == 0
                           else Prescription.Status.PARTIAL)
    prescription.save(update_fields=['status', 'updated_at'])
    log_event(actor=user, request=request, action=AuditEvent.Action.CREATE, obj=dispensing,
              changes={'prescription_status': [old, prescription.status]})
    return dispensing


def cancel_prescription(prescription, reason, user, request=None):
    if not prescription.is_open:
        raise StockError('Only prescriptions awaiting dispensing can be cancelled.')
    old = prescription.status
    prescription.status = Prescription.Status.CANCELLED
    prescription.cancel_reason = reason[:255]
    prescription.save(update_fields=['status', 'cancel_reason', 'updated_at'])
    log_event(actor=user, request=request, action=AuditEvent.Action.STATUS, obj=prescription,
              changes={'status': [old, 'cancelled']}, reason=reason)


def medications_with_stock(facilities):
    """Annotate usable (non-expired) stock on hand across the given facilities."""
    today = timezone.localdate()
    usable = Q(batches__facility__in=facilities, batches__expiry_date__gte=today)
    return Medication.objects.annotate(
        on_hand=Coalesce(Sum('batches__quantity_on_hand', filter=usable), 0),
        expired_on_hand=Coalesce(Sum('batches__quantity_on_hand', filter=Q(batches__facility__in=facilities,
                                                                           batches__expiry_date__lt=today)), 0),
    )


def low_stock_rows(facilities):
    """(facility, medication, on_hand) where usable stock is below the reorder level."""
    today = timezone.localdate()
    rows = []
    medications = list(Medication.objects.filter(is_active=True, reorder_level__gt=0))
    if not medications:
        return rows
    totals = {}
    for row in (StockBatch.objects.filter(facility__in=facilities, expiry_date__gte=today,
                                          medication__in=medications)
                .values('facility_id', 'medication_id').annotate(total=Sum('quantity_on_hand'))):
        totals[(row['facility_id'], row['medication_id'])] = row['total']
    for facility in facilities:
        for medication in medications:
            on_hand = totals.get((facility.pk, medication.pk), 0)
            if on_hand < medication.reorder_level:
                rows.append((facility, medication, on_hand))
    return rows
