from django import forms

from core.forms import BootstrapForm, BootstrapModelForm, PatientChildForm
from facilities.scope import facilities_for_user

from .models import Patient, PatientIdentifier, PatientTransfer


class PatientForm(BootstrapModelForm):
    confirm_not_duplicate = forms.BooleanField(
        required=False, label='I have reviewed the possible matches and this is a different person')

    class Meta:
        model = Patient
        fields = ['first_name', 'middle_name', 'last_name', 'sex', 'date_of_birth', 'dob_estimated',
                  'phone', 'alternate_phone', 'sms_consent', 'residence_district', 'residence_ward', 'address',
                  'next_of_kin_name', 'next_of_kin_phone', 'registration_facility', 'registration_date']

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.duplicates = []
        self.fields['registration_facility'].queryset = facilities_for_user(user)
        if self.instance.pk:
            # Registration facility and date are historical facts; corrections go through admin.
            del self.fields['registration_facility']
            del self.fields['registration_date']
            del self.fields['confirm_not_duplicate']

    def clean(self):
        from .services import find_possible_duplicates

        cleaned = super().clean()
        if self.instance.pk is None:
            self.duplicates = list(find_possible_duplicates(
                first_name=cleaned.get('first_name', ''), last_name=cleaned.get('last_name', ''),
                date_of_birth=cleaned.get('date_of_birth'), phone=cleaned.get('phone', ''),
            )[:10])
            if self.duplicates and not cleaned.get('confirm_not_duplicate'):
                raise forms.ValidationError(
                    'Possible duplicate patients were found. Review them below; if this is a different person, '
                    'tick the confirmation box and save again.')
        return cleaned


class PatientIdentifierForm(PatientChildForm):
    class Meta:
        model = PatientIdentifier
        fields = ['id_type', 'value', 'issuer', 'is_verified']


class PatientTransferForm(PatientChildForm):
    class Meta:
        model = PatientTransfer
        fields = ['to_facility', 'reason', 'study_area_note', 'request_date', 'effective_date', 'handover_notes']

    def clean_to_facility(self):
        facility = self.cleaned_data['to_facility']
        if self.patient and facility == self.patient.current_facility:
            raise forms.ValidationError('The patient is already assigned to this facility.')
        return facility


class RecordDeathForm(BootstrapForm):
    date_of_death = forms.DateField()
    cause = forms.CharField(label='Cause / circumstances', max_length=255)


class TransferActionForm(BootstrapForm):
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 2}))
