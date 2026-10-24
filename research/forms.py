from django import forms

from core.forms import BootstrapModelForm, PatientChildForm

from .models import ConsentRecord, Study, StudyEligibilityAssessment, StudyEnrollment


class StudyForm(BootstrapModelForm):
    class Meta:
        model = Study
        fields = ['code', 'title', 'protocol_number', 'principal_investigator', 'status', 'diseases', 'sites',
                  'current_consent_version', 'participant_id_prefix', 'start_date', 'end_date',
                  'eligibility_criteria', 'description']
        widgets = {'diseases': forms.CheckboxSelectMultiple}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from clinical.models import Disease
        self.fields['diseases'].queryset = Disease.objects.filter(is_active=True)


class EligibilityForm(PatientChildForm):
    class Meta:
        model = StudyEligibilityAssessment
        fields = ['study', 'assessed_on', 'is_eligible', 'criteria_notes', 'reason']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['study'].queryset = Study.objects.filter(status='active')
        self.fields['is_eligible'].widget = forms.Select(choices=[(True, 'Eligible'), (False, 'Not eligible')],
                                                         attrs={'class': 'form-select'})
        self.fields['is_eligible'].required = False


class ConsentForm(PatientChildForm):
    class Meta:
        model = ConsentRecord
        fields = ['study', 'consent_type', 'consent_version', 'decision', 'consent_date', 'witness_name',
                  'legally_authorized_representative', 'withdrawal_date', 'withdrawal_reason', 'notes']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['study'].queryset = Study.objects.exclude(status='planned')


class StudyEnrollmentForm(PatientChildForm):
    class Meta:
        model = StudyEnrollment
        fields = ['study', 'participant_id', 'facility', 'eligibility', 'consent', 'enrollment_date', 'status',
                  'outcome_date', 'outcome_reason']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.patient = self.patient
        self.fields['study'].queryset = Study.objects.filter(status='active') if not self.instance.pk else Study.objects.all()
        self.fields['eligibility'].queryset = self.patient.study_eligibility.filter(is_eligible=True).select_related('study')
        self.fields['consent'].queryset = self.patient.consents.select_related('study')
        self.fields['eligibility'].help_text = 'Only eligible assessments are listed.'
