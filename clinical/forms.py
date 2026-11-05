from django import forms
from django.utils import timezone

from core.forms import BootstrapForm, BootstrapModelForm, PatientChildForm, apply_bootstrap
from facilities.forms import LocationFieldsMixin
from facilities.models import Facility
from facilities.scope import facilities_for_user
from patients.forms import PatientForm
from research.services import research_consent_given

from .models import (ClinicalProgramme, FamilyMember, Diagnosis, Disease, PreScreening, ProgrammeEnrollment, Screening,
                     ScreeningSession)


YES_NO = [(True, 'Yes'), (False, 'No')]


class PreScreeningForm(PatientChildForm):
    """The one screening form, laid out like the old EDCS screening form. When a lab test is requested it also
    records what the subject is screened for (one Screening per disease), known NCD or not."""

    SECTIONS = [
        ('Screening', 'bi-clipboard2-check', ['date', 'screening_type', 'facility']),
        ('Consent', 'bi-pen', ['consent_obtained', 'consent_date']),
        ('Residence', 'bi-house', ['resides_in_area', 'expected_stay_months']),
        ('NCD and tests', 'bi-droplet-half', ['known_ncd', 'known_diseases', 'lab_test_requested', 'lab_request_date',
                                              'screen_for']),
        ('Outcome', 'bi-signpost-split', ['disposition', 'notes']),
    ]

    screen_for = forms.ModelMultipleChoiceField(queryset=Disease.objects.none(), widget=forms.CheckboxSelectMultiple,
                                                required=False, label='Screen for',
                                                help_text='What the investigations will look for. The doctor decides after the results.')

    class Meta:
        model = PreScreening
        fields = ['date', 'screening_type', 'facility', 'consent_obtained', 'consent_date', 'resides_in_area',
                  'expected_stay_months', 'known_ncd', 'known_diseases', 'lab_test_requested', 'lab_request_date', 'disposition', 'notes']
        widgets = {'consent_obtained': forms.RadioSelect(choices=YES_NO), 'known_ncd': forms.RadioSelect(choices=YES_NO),
                   'known_diseases': forms.CheckboxSelectMultiple,
                   'lab_test_requested': forms.RadioSelect(choices=YES_NO),
                   'resides_in_area': forms.RadioSelect(choices=YES_NO), 'notes': forms.Textarea(attrs={'rows': 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['consent_obtained'].required = True
        self.fields['disposition'].required = False
        self.fields['known_diseases'].queryset = Disease.objects.filter(is_active=True)
        self.fields['resides_in_area'].required = False
        self.fields['consent_date'].help_text = 'Required when the person consented.'
        self.fields['lab_request_date'].help_text = 'When the investigations were requested.'
        # Screening is done once per disease; a negative one is repeated with Re-screen on the Screening tab.
        screen_for = self.fields['screen_for']
        screen_for.queryset = Disease.objects.filter(is_active=True)
        if self.patient is not None:
            already = Disease.objects.filter(pk__in=self.patient.screenings.values('disease'))
            screen_for.queryset = screen_for.queryset.exclude(pk__in=already)
            if already:
                screen_for.help_text += f' Already screened for: {", ".join(d.name for d in already)}.'
            if not screen_for.queryset.exists():
                del self.fields['screen_for']

    def sections(self):
        return [(title, icon, [self[name] for name in names if name in self.fields]) for title, icon, names in self.SECTIONS]

    def clean(self):
        data = super().clean()
        if data.get('consent_obtained') is None:
            self.add_error('consent_obtained', 'Answer Yes or No.')
        if data.get('consent_obtained') and not data.get('consent_date'):
            self.add_error('consent_date', 'Enter the date of consent.')
        # The outcome follows consent: without it the person stops here; a referral is the one choice made by hand.
        if data.get('consent_obtained') is False and data.get('disposition') in ('', None, PreScreening.Disposition.PROCEED):
            data['disposition'] = PreScreening.Disposition.NOT_NOW
        elif not data.get('disposition'):
            data['disposition'] = PreScreening.Disposition.PROCEED
        if data.get('known_ncd') and not data.get('known_diseases'):
            self.add_error('known_diseases', 'Tick the known NCD: one or more.')
        if not data.get('known_ncd'):
            data['known_diseases'] = Disease.objects.none()
        if data.get('lab_test_requested') and not data.get('lab_request_date'):
            data['lab_request_date'] = data.get('date')
        if not data.get('lab_test_requested'):
            data['screen_for'] = Disease.objects.none()
        elif 'screen_for' in self.fields and not self.patient.screenings.exists() and not data.get('screen_for'):
            self.add_error('screen_for', 'Tick what the subject is screened for: one or more.')
        if data.get('screen_for') and data.get('consent_obtained') is False:
            self.add_error('screen_for', 'Without consent the subject cannot be screened.')
        if not self.instance.pk and self.patient and self.patient.prescreenings.exists():
            raise forms.ValidationError('Screening is done once per subject. Edit the existing one instead.')
        return data


class ScreeningCreateForm(PatientChildForm):
    """What the subject is going to be screened for; one record per disease. The doctor's decision gives the result."""

    diseases = forms.ModelMultipleChoiceField(queryset=Disease.objects.filter(is_active=True),
                                              widget=forms.CheckboxSelectMultiple, label='Screen for')

    class Meta:
        model = Screening
        fields = ['diseases', 'screening_date', 'facility', 'encounter', 'notes']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Screening is done once per disease: a result is added by editing the existing screening.
        self.fields['diseases'].queryset = Disease.objects.filter(is_active=True).exclude(
            pk__in=self.patient.screenings.values('disease'))


class RescreenForm(BootstrapForm):
    screening_date = forms.DateField(label='Date of re-screening', initial=timezone.localdate)
    reason = forms.CharField(label='Why is the subject being re-screened?', max_length=255,
                             widget=forms.Textarea(attrs={'rows': 2}))

    def __init__(self, *args, previous=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.previous = previous

    def clean_screening_date(self):
        date = self.cleaned_data['screening_date']
        if date < self.previous.screening_date:
            raise forms.ValidationError(f'Must not be before the earlier screening ({self.previous.screening_date:%d %b %Y}).')
        return date


class ScreeningUpdateForm(PatientChildForm):
    class Meta:
        model = Screening
        fields = ['facility', 'encounter', 'source', 'location_type', 'screening_date', 'result',
                  'risk_factors', 'consent_obtained', 'assent_obtained', 'guardian_name', 'referred', 'referred_to',
                  'next_action', 'notes']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.user is not None and not self.user.has_perm('clinical.add_diagnosis'):
            del self.fields['result']  # the result is the doctor's decision


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


ENROLL_RULE = 'Enrolling needs a diagnosis of that disease confirmed by the doctor.'
CONSENT_RULE = 'Enrolling also joins the PEN-Plus study, so it needs research consent, recorded at pre-screening.'


class ProgrammeEnrollmentCreateForm(PatientChildForm):
    """Enroll in the programme of every disease the doctor confirmed, one enrollment each."""

    programmes = forms.ModelMultipleChoiceField(queryset=None, widget=forms.CheckboxSelectMultiple, label='Programmes',
                                                help_text=ENROLL_RULE)

    class Meta:
        model = ProgrammeEnrollment
        fields = ['programmes', 'facility', 'enrollment_date', 'responsible_team', 'care_plan']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        confirmed = self.patient.diagnoses.filter(status=Diagnosis.Status.CONFIRMED).values('disease')
        enrolled = self.patient.programme_enrollments.filter(status__in=['pending', 'active']).values('programme')
        programmes = ClinicalProgramme.objects.filter(is_active=True, disease__is_active=True, disease__in=confirmed) \
            .exclude(pk__in=enrolled)
        self.fields['programmes'].queryset = programmes
        self.fields['programmes'].error_messages['invalid_choice'] = ENROLL_RULE
        self.initial.setdefault('programmes', list(programmes))
        self.has_consent = research_consent_given(self.patient)
        self.can_enroll = programmes.exists() and self.has_consent
        self.instance.patient = self.patient

    def clean(self):
        cleaned = super().clean()
        if not self.has_consent:
            raise forms.ValidationError(CONSENT_RULE)
        return cleaned


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
                                   f'Known {disease.name} subject (diagnosed or enrolled) – excluded from screening.')


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
    visit_count = forms.TypedChoiceField(coerce=int, initial=1, label='Number of future visits',
                                         choices=[(n, str(n)) for n in range(1, 13)])

    def __init__(self, *args, enrollment=None, **kwargs):
        self.enrollment = enrollment
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('create_schedule') and not cleaned.get('visit_count'):
            self.add_error('visit_count', 'Choose how many future visits to book (1 to 12).')
        return cleaned

    def clean_return_date(self):
        date = self.cleaned_data['return_date']
        if self.enrollment.outcome_date and date < self.enrollment.outcome_date:
            raise forms.ValidationError('The return date cannot be before the date the enrollment was closed.')
        return date


class DiagnosisCreateForm(PatientChildForm):
    """Record one or more diagnoses at once: tick every condition / sub-type the patient has, in any disease."""

    conditions = forms.ModelMultipleChoiceField(queryset=None, widget=forms.CheckboxSelectMultiple, required=False)

    class Meta:
        model = Diagnosis
        fields = ['disease', 'conditions', 'description', 'code', 'onset_date', 'diagnosis_date', 'encounter',
                  'complications', 'notes']
        labels = {'description': 'Description / details', 'encounter': 'Visit'}
        widgets = {'disease': forms.HiddenInput}
        help_texts = {'description': 'Added to each ticked condition. If the condition is not listed, write the '
                                     'diagnosis here; it is recorded under the disease you have open.'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from diseases.models import Condition
        self.fields['disease'].queryset = Disease.objects.filter(is_active=True)
        self.fields['disease'].required = False
        # "Cardiac, diagnosis not yet recorded" only holds imported EDCS records.
        self.fields['conditions'].queryset = (Condition.objects.filter(is_active=True, disease__is_active=True)
                                              .exclude(code='cardiac-unspecified').select_related('disease', 'parent')
                                              .order_by('disease__order', 'parent__order', 'parent_id', 'order'))
        self.fields['description'].required = False
        self.statuses = {}

    def chosen_statuses(self):
        """The status picked for each disease, posted as status_<disease id>; nothing is picked in advance."""
        return {key[len('status_'):]: value for key, value in self.data.items()
                if key.startswith('status_') and value in Diagnosis.Status.values}

    def clean(self):
        cleaned = super().clean()
        conditions = cleaned.get('conditions') or []
        if not conditions and not cleaned.get('description'):
            self.add_error('conditions', 'Tick at least one condition, or write the diagnosis in the description.')
        elif not conditions and not cleaned.get('disease'):
            self.add_error('disease', 'Choose the disease for this diagnosis.')
        diseases = {c.disease for c in conditions} or ({cleaned['disease']} if cleaned.get('disease') else set())
        chosen = self.chosen_statuses()
        for disease in sorted(diseases, key=lambda d: d.name):
            if str(disease.pk) in chosen:
                self.statuses[disease.pk] = chosen[str(disease.pk)]
            else:
                self.add_error(None, f'Choose whether the {disease.name.lower()} diagnosis is confirmed, suspected or ruled out.')
        return cleaned


class FamilyMemberForm(PatientChildForm):
    class Meta:
        model = FamilyMember
        fields = ['relation', 'sex', 'age_years', 'alive', 'scd_status', 'tested', 'test_date', 'test_type',
                  'referred_for_screening', 'recorded_on', 'notes']
        widgets = {'alive': forms.NullBooleanSelect, 'tested': forms.NullBooleanSelect}
        help_texts = {'scd_status': 'Do not record names: relatives are not registered subjects.'}
