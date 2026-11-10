from django import forms

from core.forms import BootstrapForm, BootstrapModelForm, PatientChildForm
from facilities.forms import LocationFieldsMixin
from facilities.scope import clinic_sites_for_user, default_facility, facilities_for_user

from .models import Patient, PatientIdentifier, PatientTransfer


class ConsentSelect(forms.NullBooleanSelect):
    def __init__(self, attrs=None):
        super().__init__(attrs)
        self.choices = [('unknown', 'Not asked yet'), ('true', 'Yes – agrees to SMS'), ('false', 'No – declined SMS')]


class PatientForm(LocationFieldsMixin, BootstrapModelForm):
    confirm_not_duplicate = forms.BooleanField(
        required=False, label='I have reviewed the possible matches and this is a different person')

    class Meta:
        model = Patient
        fields = ['first_name', 'middle_name', 'last_name', 'sex', 'date_of_birth', 'dob_estimated',
                  'phone', 'alternate_phone', 'sms_consent', 'residence_district', 'residence_ward', 'residence_street',
                  'address',
                  'next_of_kin_name', 'next_of_kin_phone', 'registration_facility', 'registration_date']

    location_fields = {'region': 'residence_region', 'district': 'residence_district', 'ward': 'residence_ward',
                       'street': 'residence_street'}

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['sms_consent'].widget = ConsentSelect(attrs={'class': 'form-select'})
        facility = default_facility(user) if user and not self.instance.pk else None
        self.setup_locations(default_district=facility.district if facility else None)
        self.order_location_fields(before='address')
        self.duplicates = []
        # Subjects are registered only at the PEN-Plus clinic sites the user works at.
        sites = clinic_sites_for_user(user)
        self.fields['registration_facility'].queryset = sites
        if len(sites) == 1 and not self.instance.pk:
            self.initial['registration_facility'] = sites[0].pk
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
                    'Possible duplicate subjects were found. Review them below; if this is a different person, '
                    'tick the confirmation box and save again.')
        return cleaned


    def save(self, commit=True):
        if 'sms_consent' in self.changed_data:
            from django.utils import timezone
            self.instance.sms_consent_recorded_at = timezone.now()
        return super().save(commit)


class PatientIdentifierForm(PatientChildForm):
    class Meta:
        model = PatientIdentifier
        fields = ['id_type', 'value', 'issuer', 'is_verified']


class PatientTransferForm(PatientChildForm):
    class Meta:
        model = PatientTransfer
        fields = ['to_facility', 'reason', 'study_area_note', 'request_date', 'effective_date', 'handover_notes']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # A transfer moves the subject to another PEN-Plus clinic site (anywhere, not only the user's own).
        from facilities.models import Facility
        self.fields['to_facility'].queryset = Facility.objects.filter(is_active=True, is_research_site=True).exclude(
            pk=getattr(self.patient, 'current_facility_id', None)).order_by('name')
        self.fields['to_facility'].help_text = ('Another PEN-Plus clinic site. To send the subject to a hospital '
                                                'outside PEN-Plus, use Refer instead: the subject stays enrolled.')

    def clean_to_facility(self):
        facility = self.cleaned_data['to_facility']
        if self.patient and facility == self.patient.current_facility:
            raise forms.ValidationError('The subject is already assigned to this facility.')
        return facility


DEATH_CAUSES = [('ncd', 'NCD'), ('unknown', 'Unknown'), ('other', 'Other')]


class RecordDeathForm(BootstrapForm):
    date_of_death = forms.DateField()
    cause_category = forms.ChoiceField(label='Cause of death', choices=[('', 'Choose…')] + DEATH_CAUSES)
    cause = forms.CharField(label='Cause / circumstances', max_length=255,
                            help_text='What is known; for "Other", say what the cause was.')


class TransferActionForm(BootstrapForm):
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 2}))
