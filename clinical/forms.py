from django import forms

from core.forms import BootstrapForm, BootstrapModelForm, PatientChildForm, apply_bootstrap
from facilities.forms import LocationFieldsMixin
from facilities.models import Facility
from facilities.scope import facilities_for_user
from patients.forms import PatientForm

from .models import (ClinicalProgramme, FamilyMember, Diagnosis, Disease, PreScreening, ProgrammeEnrollment, Screening,
                     ScreeningSession)


class PreScreeningForm(PatientChildForm):
    class Meta:
        model = PreScreening
        fields = ['facility', 'date', 'resides_in_area', 'expected_stay_months', 'disposition', 'notes']


class ScreeningCreateForm(PatientChildForm):
    """Screen for several diseases at once; one Screening record is created per disease."""

    diseases = forms.ModelMultipleChoiceField(queryset=Disease.objects.filter(is_active=True),
                                              widget=forms.CheckboxSelectMultiple)

    class Meta:
        model = Screening
        fields = ['diseases', 'facility', 'encounter', 'source', 'location_type', 'screening_date', 'result',
                  'risk_factors', 'consent_obtained', 'referred', 'referred_to', 'next_action', 'notes']
        help_texts = {'result': 'Applied to every selected disease; edit individual screenings afterwards if results differ.'}


class ScreeningUpdateForm(PatientChildForm):
    class Meta:
        model = Screening
        fields = ['facility', 'encounter', 'source', 'location_type', 'screening_date', 'result',
                  'risk_factors', 'consent_obtained', 'assent_obtained', 'guardian_name', 'referred', 'referred_to',
                  'next_action', 'notes']


class DiagnosisForm(PatientChildForm):
    class Meta:
        model = Diagnosis
        fields = ['disease', 'description', 'condition', 'code', 'status', 'onset_date', 'diagnosis_date', 'encounter',
                  'complications', 'notes']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from diseases.models import Condition
        self.fields['disease'].queryset = Disease.objects.filter(is_active=True)
        self.fields['condition'].queryset = (Condition.objects.filter(is_active=True, disease__is_active=True)
                                             .select_related('parent').order_by('disease__order', 'parent__order', 'parent_id', 'order'))
        self.fields['condition'].label_from_instance = lambda obj: obj.label
        self.fields['condition'].required = False
        self.fields['condition'].help_text = 'Leave empty to set it from the description.'


class ProgrammeEnrollmentForm(PatientChildForm):
    class Meta:
        model = ProgrammeEnrollment
        fields = ['programme', 'facility', 'enrollment_date', 'status', 'responsible_team', 'care_plan',
                  'outcome_date', 'outcome_reason']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['programme'].queryset = ClinicalProgramme.objects.filter(is_active=True, disease__is_active=True)
        self.instance.patient = self.patient


class ScreeningSessionForm(LocationFieldsMixin, BootstrapModelForm):
    location_fields = {'region': 'region', 'district': 'district', 'ward': 'ward', 'street': 'street'}

    class Meta:
        model = ScreeningSession
        fields = ['name', 'facility', 'session_date', 'source', 'location_type', 'place_name', 'district', 'ward',
                  'street', 'diseases', 'min_age', 'team_notes', 'is_closed']
        widgets = {'diseases': forms.CheckboxSelectMultiple}

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['facility'].queryset = facilities_for_user(user)
        self.fields['diseases'].queryset = Disease.objects.filter(is_active=True)
        if not self.instance.pk:
            del self.fields['is_closed']
        facility = facilities_for_user(user).first() if user else None
        self.setup_locations(default_district=facility.district if facility and not self.instance.pk else None)
        self.order_location_fields(before='diseases')


class SessionScreeningForm(BootstrapForm):
    """Eligibility, consent and one result per disease for a person screened in a session."""

    permanent_resident = forms.BooleanField(required=False, label='Permanent resident of the area')
    consent_obtained = forms.BooleanField(required=False, label='Consent obtained')
    assent_obtained = forms.BooleanField(required=False, label='Assent obtained (participant under 18)')
    guardian_name = forms.CharField(max_length=150, required=False, label='Parent / guardian who consented',
                                    help_text='Required for participants under 18.')
    risk_factors = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 2}))
    referred = forms.BooleanField(required=False, label='Informed of result and referred')
    referred_to = forms.ModelChoiceField(queryset=Facility.objects.filter(is_active=True), required=False)
    next_action = forms.CharField(max_length=255, required=False)
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 2}))

    def __init__(self, *args, session=None, **kwargs):
        self.session = session
        super().__init__(*args, **kwargs)
        self.diseases = list(session.diseases.all())
        for disease in self.diseases:
            self.fields[f'result_{disease.code}'] = forms.ChoiceField(
                label=f'{disease.name} result', required=False,
                choices=[('', 'Not screened')] + list(Screening.Result.choices))
        apply_bootstrap(self.fields)
        # Show the disease results first.
        order = [f'result_{d.code}' for d in self.diseases]
        self.order_fields(order + [name for name in self.fields if name not in order])

    def result_fields(self):
        return [self[f'result_{d.code}'] for d in self.diseases]

    def detail_fields(self):
        return [self[name] for name in self.fields if not name.startswith('result_')]

    def results(self):
        return [(d, self.cleaned_data[f'result_{d.code}']) for d in self.diseases if self.cleaned_data.get(f'result_{d.code}')]

    def clean(self):
        cleaned = super().clean()
        if not any(cleaned.get(f'result_{d.code}') for d in self.diseases):
            raise forms.ValidationError('Enter a result for at least one disease.')
        if not cleaned.get('consent_obtained'):
            self.add_error('consent_obtained', 'Only consenting individuals can be screened.')
        if not cleaned.get('permanent_resident'):
            self.add_error('permanent_resident', 'Non-permanent residents are excluded by the protocol.')
        positive = any(cleaned.get(f'result_{d.code}') == 'positive' for d in self.diseases)
        if positive and not cleaned.get('referred'):
            self.add_error('referred', 'Everyone with a positive result must be informed and referred.')
        if cleaned.get('referred') and not cleaned.get('referred_to'):
            self.add_error('referred_to', 'Select the facility the person was referred to.')
        return cleaned

    def check_person(self, age, patient=None):
        """Protocol eligibility that depends on who is being screened."""
        if age < self.session.min_age:
            self.add_error(None, f'Not eligible: age {age} is below the minimum of {self.session.min_age} years.')
        if age < 18:
            if not self.cleaned_data.get('assent_obtained'):
                self.add_error('assent_obtained', 'Assent is required for participants under 18.')
            if not self.cleaned_data.get('guardian_name'):
                self.add_error('guardian_name', 'Record the parent/guardian who gave consent.')
        if patient is not None:
            for disease, _result in self.results():
                known = (patient.diagnoses.filter(disease=disease, status='confirmed').exists()
                         or patient.programme_enrollments.filter(programme__disease=disease,
                                                                 status__in=['pending', 'active']).exists())
                if known:
                    self.add_error(f'result_{disease.code}',
                                   f'Known {disease.name} patient (diagnosed or enrolled) – excluded from screening.')


class PatientQuickForm(PatientForm):
    """Patient registration inside a screening session; facility and date come from the session."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ('registration_facility', 'registration_date', 'alternate_phone'):
            self.fields.pop(name, None)
        self.fields['next_of_kin_name'].label = 'Next of kin / parent or guardian'


class ReturnToCareForm(BootstrapForm):
    KIND_CHOICES = [('return', 'Returned after default / lost to follow-up'),
                    ('return_withdrawal', 'Returned after withdrawal / stopping treatment'),
                    ('transfer_in', 'Transferred back in'),
                    ('return_other', 'Other return')]
    return_date = forms.DateField()
    kind = forms.ChoiceField(choices=KIND_CHOICES, label='Type of return')
    reason = forms.CharField(max_length=255, required=False, label='Notes')
    create_schedule = forms.BooleanField(required=False, initial=True,
                                         label='Create a follow-up schedule starting from the return date')
    interval_months = forms.TypedChoiceField(coerce=int, initial=1, label='Visit interval', choices=[
        (1, 'Monthly'), (2, 'Every 2 months'), (3, 'Every 3 months'), (6, 'Every 6 months')])
    visit_count = forms.IntegerField(min_value=1, max_value=36, initial=6, label='Number of future visits')

    def __init__(self, *args, enrollment=None, **kwargs):
        self.enrollment = enrollment
        super().__init__(*args, **kwargs)

    def clean_return_date(self):
        date = self.cleaned_data['return_date']
        if self.enrollment.outcome_date and date < self.enrollment.outcome_date:
            raise forms.ValidationError('The return date cannot be before the date the enrollment was closed.')
        return date


class ConditionChoiceField(forms.ModelMultipleChoiceField):
    def label_from_instance(self, obj):
        return f'{obj.disease.name}: {obj.label}'


class DiagnosisCreateForm(PatientChildForm):
    """Record one or more diagnoses at once: tick every condition / sub-type the patient has."""

    conditions = ConditionChoiceField(queryset=None, widget=forms.CheckboxSelectMultiple, required=False,
                                      label='Conditions (tick all that apply)',
                                      help_text='Choose a sub-type when known, e.g. Rheumatic heart disease – Mixed mitral '
                                                'valve disease. One diagnosis is recorded for each ticked condition.')

    class Meta:
        model = Diagnosis
        fields = ['disease', 'conditions', 'description', 'code', 'status', 'onset_date', 'diagnosis_date', 'encounter',
                  'complications', 'notes']
        labels = {'description': 'Description / details (optional)'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from diseases.models import Condition
        self.fields['disease'].queryset = Disease.objects.filter(is_active=True)
        self.fields['conditions'].queryset = (Condition.objects.filter(is_active=True, disease__is_active=True)
                                              .select_related('disease', 'parent')
                                              .order_by('disease__order', 'parent__order', 'parent_id', 'order'))
        self.fields['description'].required = False

    def clean(self):
        cleaned = super().clean()
        disease, conditions = cleaned.get('disease'), cleaned.get('conditions') or []
        if disease and any(c.disease_id != disease.pk for c in conditions):
            self.add_error('conditions', f'Choose only conditions of {disease}.')
        if not conditions and not cleaned.get('description'):
            self.add_error('conditions', 'Tick at least one condition, or write the diagnosis in the description.')
        return cleaned


class FamilyMemberForm(PatientChildForm):
    class Meta:
        model = FamilyMember
        fields = ['relation', 'sex', 'age_years', 'alive', 'scd_status', 'tested', 'test_date', 'test_type',
                  'referred_for_screening', 'recorded_on', 'notes']
        widgets = {'alive': forms.NullBooleanSelect, 'tested': forms.NullBooleanSelect}
        help_texts = {'scd_status': 'Do not record names: relatives are not registered patients.'}
