from django.conf import settings
from django.db import models
from django.db.models import Sum
from django.utils import timezone

from core.models import SoftDeleteModel, TimeStampedModel
from facilities.models import Facility
from patients.models import Patient

EXPIRY_WARNING_DAYS = 90


class Medication(models.Model):
    """A stock item: a medicine, or a supply used by the lab, imaging or clinic (strips, cartridges, electrodes)."""

    class ItemType(models.TextChoices):
        MEDICINE = 'medicine', 'Medicine'
        LAB_SUPPLY = 'lab_supply', 'Laboratory reagent / supply'
        IMAGING_SUPPLY = 'imaging_supply', 'ECG / echo / imaging supply'
        MEDICAL_SUPPLY = 'medical_supply', 'Medical supply (syringes, strips, …)'
        EQUIPMENT = 'equipment', 'Equipment'

    class DosageForm(models.TextChoices):
        TABLET = 'tablet', 'Tablet'
        CAPSULE = 'capsule', 'Capsule'
        INJECTION = 'injection', 'Injection'
        SYRUP = 'syrup', 'Syrup / suspension'
        INHALER = 'inhaler', 'Inhaler'
        TOPICAL = 'topical', 'Topical'
        OTHER = 'other', 'Other'

    item_type = models.CharField(max_length=20, choices=ItemType.choices, default=ItemType.MEDICINE, db_index=True)
    code = models.SlugField(max_length=40, unique=True)
    name = models.CharField('Generic name', max_length=150)
    strength = models.CharField(max_length=50, blank=True, help_text='e.g. 500 mg, 100 IU/mL')
    dosage_form = models.CharField(max_length=20, choices=DosageForm.choices, default=DosageForm.TABLET)
    dispensing_unit = models.CharField(max_length=30, default='tablet', help_text='Unit counted in stock, e.g. tablet, vial, bottle')
    reorder_level = models.PositiveIntegerField(default=0, help_text='Low-stock warning threshold per facility.')
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name', 'strength']
        verbose_name = 'stock item'

    def __str__(self):
        if self.item_type != self.ItemType.MEDICINE:
            return ' '.join(part for part in (self.name, self.strength) if part)
        return ' '.join(part for part in (self.name, self.strength, self.get_dosage_form_display().lower()) if part)

    @property
    def is_medicine(self):
        return self.item_type == self.ItemType.MEDICINE


class StockBatch(TimeStampedModel):
    """A received batch of one medication at one facility. Quantity changes only through StockMovement."""

    medication = models.ForeignKey(Medication, on_delete=models.PROTECT, related_name='batches')
    facility = models.ForeignKey(Facility, on_delete=models.PROTECT, related_name='stock_batches')
    batch_number = models.CharField(max_length=60)
    expiry_date = models.DateField()
    supplier = models.CharField(max_length=150, blank=True)
    received_date = models.DateField(default=timezone.localdate)
    quantity_on_hand = models.PositiveIntegerField(default=0, editable=False)

    class Meta:
        ordering = ['expiry_date', 'pk']
        verbose_name_plural = 'stock batches'
        constraints = [models.UniqueConstraint(fields=['medication', 'facility', 'batch_number'],
                                               name='unique_batch_per_facility')]

    def __str__(self):
        return f'{self.medication} · batch {self.batch_number} · exp {self.expiry_date:%d %b %Y} · {self.quantity_on_hand} left'

    @property
    def is_expired(self):
        return self.expiry_date < timezone.localdate()

    @property
    def expires_soon(self):
        return not self.is_expired and (self.expiry_date - timezone.localdate()).days <= EXPIRY_WARNING_DAYS


class StockMovement(models.Model):
    """Append-only stock ledger: every change to a batch quantity, who made it and why."""

    class MovementType(models.TextChoices):
        RECEIPT = 'receipt', 'Receipt'
        DISPENSE = 'dispense', 'Dispensed'
        ADJUSTMENT = 'adjustment', 'Adjustment'
        WRITE_OFF = 'write_off', 'Write-off (expired / damaged)'
        CONSUMPTION = 'consumption', 'Used for a test / investigation'

    batch = models.ForeignKey(StockBatch, on_delete=models.PROTECT, related_name='movements')
    movement_type = models.CharField(max_length=20, choices=MovementType.choices)
    quantity = models.IntegerField(help_text='Positive adds stock, negative removes it.')
    balance_after = models.PositiveIntegerField()
    reason = models.CharField(max_length=255, blank=True)
    dispensing = models.ForeignKey('Dispensing', on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    lab_order = models.ForeignKey('laboratory.LabOrder', on_delete=models.PROTECT, null=True, blank=True,
                                  related_name='stock_movements')
    investigation = models.ForeignKey('investigations.InvestigationOrder', on_delete=models.PROTECT, null=True,
                                      blank=True, related_name='stock_movements')
    performed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-timestamp', '-pk']
        permissions = [('adjust_stock', 'Can adjust or write off stock')]

    def __str__(self):
        return f'{self.get_movement_type_display()} {self.quantity:+d} ({self.batch.batch_number})'

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValueError('Stock movements cannot be modified; record a correcting adjustment instead.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError('Stock movements cannot be deleted.')


class Prescription(SoftDeleteModel):
    class Route(models.TextChoices):
        ORAL = 'oral', 'Oral'
        IV = 'iv', 'Intravenous'
        IM = 'im', 'Intramuscular'
        SC = 'sc', 'Subcutaneous'
        INHALED = 'inhaled', 'Inhaled'
        TOPICAL = 'topical', 'Topical'
        OTHER = 'other', 'Other'

    class Status(models.TextChoices):
        PRESCRIBED = 'prescribed', 'Awaiting dispensing'
        PARTIAL = 'partially_dispensed', 'Partially dispensed'
        DISPENSED = 'dispensed', 'Dispensed'
        CANCELLED = 'cancelled', 'Cancelled'

    OPEN_STATUSES = ('prescribed', 'partially_dispensed')

    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name='prescriptions')
    encounter = models.ForeignKey('encounters.Encounter', on_delete=models.PROTECT, null=True, blank=True,
                                  related_name='prescriptions')
    facility = models.ForeignKey(Facility, on_delete=models.PROTECT, related_name='+', help_text='Dispensing facility')
    medication = models.ForeignKey(Medication, on_delete=models.PROTECT, related_name='prescriptions')
    dose = models.CharField(max_length=50, help_text='e.g. 500 mg, 10 units')
    route = models.CharField(max_length=20, choices=Route.choices, default=Route.ORAL)
    frequency = models.CharField(max_length=50, help_text='e.g. once daily, twice daily (BD), at night')
    duration_days = models.PositiveSmallIntegerField('Duration (days)', null=True, blank=True)
    quantity = models.PositiveIntegerField('Quantity to dispense', help_text='In the medication’s dispensing unit.')
    instructions = models.CharField(max_length=255, blank=True)
    prescriber = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    prescribed_at = models.DateTimeField(default=timezone.now)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PRESCRIBED, editable=False)
    cancel_reason = models.CharField(max_length=255, blank=True, editable=False)

    class Meta:
        ordering = ['-prescribed_at']

    def __str__(self):
        return f'{self.medication} {self.dose} {self.frequency}'

    @property
    def quantity_dispensed(self):
        return self.dispensings.aggregate(total=Sum('quantity'))['total'] or 0

    @property
    def quantity_remaining(self):
        return max(self.quantity - self.quantity_dispensed, 0)

    @property
    def is_open(self):
        return self.status in self.OPEN_STATUSES


class MedicationRecord(SoftDeleteModel):
    """Medication history recorded at a visit (continue / start / stop). Not a prescription; no stock effect."""

    class Action(models.TextChoices):
        CONTINUE = 'continue', 'Continue'
        START = 'start', 'Start'
        STOP = 'stop', 'Stop'
        NOT_ELIGIBLE = 'not_eligible', 'Not eligible'

    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name='medication_records')
    encounter = models.ForeignKey('encounters.Encounter', on_delete=models.PROTECT, null=True, blank=True,
                                  related_name='medication_records')
    medication = models.ForeignKey(Medication, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    medication_name = models.CharField(max_length=150, help_text='As recorded, even if not in the catalogue.')
    action = models.CharField(max_length=20, choices=Action.choices, blank=True)
    dose = models.CharField(max_length=150, blank=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    record_date = models.DateField()
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['-record_date', 'medication_name']

    def __str__(self):
        return f'{self.medication_name} ({self.get_action_display() or "recorded"}) {self.record_date}'


class Dispensing(TimeStampedModel):
    prescription = models.ForeignKey(Prescription, on_delete=models.PROTECT, related_name='dispensings')
    batch = models.ForeignKey(StockBatch, on_delete=models.PROTECT, related_name='dispensings')
    quantity = models.PositiveIntegerField()
    dispensed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    dispensed_at = models.DateTimeField(default=timezone.now)
    counselling_notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['-dispensed_at']
        verbose_name_plural = 'dispensings'

    def __str__(self):
        return f'{self.quantity} × {self.batch.medication} for {self.prescription.patient}'


class ConsumableUse(models.Model):
    """Stock used each time a lab test is resulted or an investigation is reported, e.g. 1 HbA1c cartridge."""

    item = models.ForeignKey(Medication, on_delete=models.PROTECT, related_name='uses')
    lab_test = models.ForeignKey('laboratory.LabTest', on_delete=models.CASCADE, null=True, blank=True,
                                 related_name='consumables')
    investigation_type = models.ForeignKey('investigations.InvestigationType', on_delete=models.CASCADE, null=True,
                                           blank=True, related_name='consumables')
    quantity = models.PositiveIntegerField(default=1, help_text="Per test, in the item's stock unit.")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['item__name']
        constraints = [models.CheckConstraint(
            condition=(models.Q(lab_test__isnull=False, investigation_type__isnull=True)
                       | models.Q(lab_test__isnull=True, investigation_type__isnull=False)),
            name='consumable_for_one_service')]

    def __str__(self):
        return f'{self.quantity} {self.item.dispensing_unit} {self.item} per {self.lab_test or self.investigation_type}'


class StockShortfall(models.Model):
    """A test or investigation was done without (enough) stock of a consumable: a stock-out event."""

    item = models.ForeignKey(Medication, on_delete=models.PROTECT, related_name='shortfalls')
    facility = models.ForeignKey(Facility, on_delete=models.PROTECT, related_name='+')
    quantity_needed = models.PositiveIntegerField()
    quantity_missing = models.PositiveIntegerField()
    lab_order = models.ForeignKey('laboratory.LabOrder', on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    investigation = models.ForeignKey('investigations.InvestigationOrder', on_delete=models.PROTECT, null=True,
                                      blank=True, related_name='+')
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    resolved = models.BooleanField(default=False)
    resolution_note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['-occurred_at']

    def __str__(self):
        return f'{self.item} short by {self.quantity_missing} at {self.facility}'
