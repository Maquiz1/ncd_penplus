from django import forms
from django.utils import timezone

from core.forms import BootstrapForm, BootstrapModelForm, PatientChildForm
from facilities.scope import facilities_for_user

from .models import Medication, Prescription, StockBatch
from .services import available_batches


class MedicationForm(BootstrapModelForm):
    class Meta:
        model = Medication
        fields = ['code', 'name', 'strength', 'dosage_form', 'dispensing_unit', 'reorder_level', 'is_active']


class ReceiveStockForm(BootstrapForm):
    medication = forms.ModelChoiceField(queryset=Medication.objects.filter(is_active=True))
    facility = forms.ModelChoiceField(queryset=None)
    batch_number = forms.CharField(max_length=60)
    expiry_date = forms.DateField()
    quantity = forms.IntegerField(min_value=1, help_text='In the medication’s dispensing unit.')
    supplier = forms.CharField(max_length=150, required=False, help_text='e.g. MSD, supplier name')
    received_date = forms.DateField(initial=timezone.localdate)

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['facility'].queryset = facilities_for_user(user)

    def clean_expiry_date(self):
        expiry = self.cleaned_data['expiry_date']
        if expiry < timezone.localdate():
            raise forms.ValidationError('This batch has already expired; do not receive it into usable stock.')
        return expiry


class AdjustStockForm(BootstrapForm):
    KIND_CHOICES = [('add', 'Add stock (count correction)'), ('remove', 'Remove stock (count correction / loss)'),
                    ('write_off', 'Write off (expired / damaged)')]
    kind = forms.ChoiceField(choices=KIND_CHOICES, label='Adjustment type')
    quantity = forms.IntegerField(min_value=1)
    reason = forms.CharField(max_length=255, help_text='Required. e.g. physical count 12 Oct, damaged in storage')

    def signed_quantity(self):
        q = self.cleaned_data['quantity']
        return q if self.cleaned_data['kind'] == 'add' else -q


class PrescriptionForm(PatientChildForm):
    class Meta:
        model = Prescription
        fields = ['medication', 'dose', 'route', 'frequency', 'duration_days', 'quantity', 'instructions',
                  'facility', 'encounter']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['medication'].queryset = Medication.objects.filter(is_active=True)


class DispenseForm(BootstrapForm):
    batch = forms.ModelChoiceField(queryset=StockBatch.objects.none(), empty_label=None,
                                   help_text='Earliest-expiring batch is listed first. Expired batches are excluded.')
    quantity = forms.IntegerField(min_value=1)
    counselling_notes = forms.CharField(max_length=255, required=False, label='Counselling / notes')

    def __init__(self, *args, prescription=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.prescription = prescription
        self.fields['batch'].queryset = available_batches(prescription.medication, prescription.facility)

    def clean(self):
        cleaned = super().clean()
        batch, quantity = cleaned.get('batch'), cleaned.get('quantity')
        if batch and quantity:
            if quantity > batch.quantity_on_hand:
                self.add_error('quantity', f'Only {batch.quantity_on_hand} left in this batch.')
            if quantity > self.prescription.quantity_remaining:
                self.add_error('quantity', f'Only {self.prescription.quantity_remaining} remain on the prescription.')
        return cleaned
