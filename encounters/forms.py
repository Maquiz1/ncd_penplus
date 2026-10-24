from django import forms

from core.forms import PatientChildForm

from .models import Admission, Appointment, ClinicSession, Encounter, FollowUpPlan, Vitals


class AppointmentForm(PatientChildForm):
    class Meta:
        model = Appointment
        fields = ['facility', 'scheduled_date', 'scheduled_time', 'stage', 'location_type', 'status',
                  'programme_enrollment', 'study_enrollment', 'reason_for_change', 'notes']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            self.fields['status'].choices = [('scheduled', 'Scheduled'), ('confirmed', 'Confirmed')]
            del self.fields['reason_for_change']
        else:
            # "Attended" is set by recording an encounter, never by hand.
            self.fields['status'].choices = [c for c in Appointment.Status.choices if c[0] != 'attended'] \
                if self.instance.status != 'attended' else Appointment.Status.choices
            self.fields['reason_for_change'].help_text = 'Required when rescheduling or cancelling.'

    def clean(self):
        cleaned = super().clean()
        inst = self.instance
        if inst.pk:
            changed = cleaned.get('scheduled_date') != inst._loaded_date or cleaned.get('status') == 'cancelled'
            if changed and not cleaned.get('reason_for_change'):
                self.add_error('reason_for_change', 'Give the reason for rescheduling or cancelling.')
            if cleaned.get('scheduled_date') != inst._loaded_date and cleaned.get('status') in ('scheduled', 'confirmed'):
                cleaned['status'] = 'rescheduled'
        return cleaned


class EncounterForm(PatientChildForm):
    class Meta:
        model = Encounter
        fields = ['facility', 'appointment', 'encounter_date', 'stage', 'location_type', 'status',
                  'home_visit_outcome', 'programme_enrollment', 'study_enrollment', 'chief_complaint',
                  'clinical_notes', 'plan_notes']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.patient is not None and not self.instance.pk:
            self.fields['appointment'].queryset = self.patient.appointments.filter(
                status__in=Appointment.OPEN_STATUSES).order_by('scheduled_date')


class VitalsForm(PatientChildForm):
    class Meta:
        model = Vitals
        fields = ['taken_at', 'weight_kg', 'height_cm', 'systolic_bp', 'diastolic_bp', 'pulse', 'temperature_c',
                  'respiratory_rate', 'spo2', 'blood_glucose', 'waist_cm', 'muac_cm', 'notes']


class FollowUpPlanForm(PatientChildForm):
    class Meta:
        model = FollowUpPlan
        fields = ['facility', 'programme_enrollment', 'study_enrollment', 'interval_months', 'anchor_type',
                  'anchor_date', 'visit_count', 'end_date', 'generation', 'is_active', 'change_reason']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            del self.fields['change_reason']
            del self.fields['is_active']
        else:
            self.fields['change_reason'].required = True


class AdmissionForm(PatientChildForm):
    class Meta:
        model = Admission
        fields = ['facility', 'encounter', 'ward', 'admitted_at', 'reason', 'readmission_of']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['readmission_of'].queryset = self.patient.admissions.exclude(discharged_at=None)


class DischargeForm(PatientChildForm):
    class Meta:
        model = Admission
        fields = ['discharged_at', 'discharge_outcome', 'discharge_summary']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['discharged_at'].required = True
        self.fields['discharge_outcome'].required = True


class AppointmentStatusForm(forms.Form):
    status = forms.ChoiceField(choices=[('confirmed', 'Confirmed'), ('missed', 'Missed'), ('cancelled', 'Cancelled')])
    reason = forms.CharField(max_length=255, required=False)


class ClinicSessionForm(PatientChildForm):
    class Meta:
        model = ClinicSession
        fields = ['name', 'facility', 'start_date', 'end_date', 'start_time', 'programmes', 'include_ltfu',
                  'allocation', 'skip_weekends', 'move_existing', 'team', 'contact_phone', 'sms_template',
                  'send_without_consent']
        widgets = {'programmes': forms.CheckboxSelectMultiple, 'sms_template': forms.Textarea(attrs={'rows': 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from clinical.models import ClinicalProgramme
        self.fields['programmes'].queryset = ClinicalProgramme.objects.filter(is_active=True, disease__is_active=True)

    def clean_sms_template(self):
        template = self.cleaned_data['sms_template']
        try:
            template.format(facility='X', day='X', date='X', time='X', contact='X')
        except (KeyError, IndexError, ValueError) as exc:
            raise forms.ValidationError(f'Unknown placeholder {exc}. Use {{facility}} {{day}} {{date}} {{time}} {{contact}}.')
        return template
