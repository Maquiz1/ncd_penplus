from django import forms
from django.db.models import Q

from core.forms import PatientChildForm

from .models import Admission, Appointment, ClinicSession, Encounter, FollowUpPlan, Vitals


class AppointmentForm(PatientChildForm):
    class Meta:
        model = Appointment
        fields = ['facility', 'scheduled_date', 'scheduled_time', 'stage', 'location_type', 'status',
                  'programme_enrollment', 'study_enrollment', 'reason_for_change', 'notes']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # An appointment is a planned visit, so it is never "Unscheduled".
        self.fields['stage'].choices = [c for c in self.fields['stage'].choices if c[0] != 'unscheduled']
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
    # The programmes were set at enrollment from the doctor's confirmation, so a schedule follows the one
    # PEN-Plus study enrollment and covers all of them.
    class Meta:
        model = FollowUpPlan
        fields = ['facility', 'study_enrollment', 'interval_months', 'anchor_type',
                  'anchor_date', 'visit_count', 'end_date', 'generation', 'is_active', 'change_reason']
        labels = {'study_enrollment': 'Study'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.patient is not None:
            from research.services import MAIN_STUDY_CODE
            study = self.fields['study_enrollment']
            study.queryset = self.patient.study_enrollments.filter(study__code=MAIN_STUDY_CODE).filter(
                Q(status__in=['pending', 'active']) | Q(pk=self.instance.study_enrollment_id)).select_related('study')
            current = study.queryset.first()
            if current is not None:
                study.required = True
                study.empty_label = None
                study.help_text = 'Covers every programme the subject was enrolled in.'
                study.label_from_instance = lambda e: f'PEN-Plus · {e.participant_id}'
                if not self.instance.pk:
                    self.initial.setdefault('study_enrollment', current.pk)
                    self.initial.setdefault('anchor_date', current.enrollment_date)
        # How many visits to book ahead, 1 to 12 (an automatic schedule books at least one; 0 only for manual).
        self.fields['visit_count'].widget = forms.Select(choices=[(n, str(n)) for n in range(13)],
                                                         attrs={'class': 'form-select'})
        if not self.instance.pk:
            self.initial.setdefault('visit_count', 1)
            del self.fields['change_reason']
            del self.fields['is_active']
        else:
            self.fields['change_reason'].required = True

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('generation') == FollowUpPlan.Generation.AUTOMATIC and not cleaned.get('visit_count'):
            self.add_error('visit_count', 'Choose how many future visits to book (1 to 12).')
        return cleaned



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
