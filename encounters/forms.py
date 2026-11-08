from datetime import timedelta

from django import forms
from django.db.models import Q
from django.utils import timezone

from core.forms import PatientChildForm

from .models import Admission, Appointment, ClinicSession, Encounter, FollowUpPlan, Referral, Vitals


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
    # A schedule follows the PEN-Plus study enrollment and books visits for the programme clinics ticked. Each
    # programme follows one active schedule: a subject can attend the diabetes and the cardiac clinic on their own
    # schedules, or both on one.
    class Meta:
        model = FollowUpPlan
        fields = ['programmes', 'facility', 'study_enrollment', 'interval_months', 'anchor_type',
                  'anchor_date', 'visit_count', 'end_date', 'generation', 'is_active', 'change_reason']
        labels = {'study_enrollment': 'Study', 'programmes': 'Programme clinics'}
        widgets = {'programmes': forms.CheckboxSelectMultiple}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        programmes = self.fields['programmes']
        if self.patient is not None:
            from research.services import MAIN_STUDY_CODE
            current = self.instance.programmes.values_list('pk', flat=True) if self.instance.pk else []
            programmes.queryset = self.patient.programme_enrollments.filter(
                Q(status__in=['pending', 'active']) | Q(pk__in=list(current))).select_related('programme')
            study = self.fields['study_enrollment']
            study.queryset = self.patient.study_enrollments.filter(study__code=MAIN_STUDY_CODE).filter(
                Q(status__in=['pending', 'active']) | Q(pk=self.instance.study_enrollment_id)).select_related('study')
            enrollment = study.queryset.first()
            if enrollment is not None:
                study.required = True
                study.empty_label = None
                study.label_from_instance = lambda e: f'PEN-Plus · {e.participant_id}'
                if not self.instance.pk:
                    self.initial.setdefault('study_enrollment', enrollment.pk)
                    self.initial.setdefault('anchor_date', enrollment.enrollment_date)
        programmes.required = True
        programmes.label_from_instance = lambda e: str(e.programme)
        if not self.instance.pk:
            # Every programme ticked to start with; with one programme there is nothing to choose.
            self.initial.setdefault('programmes', [e.pk for e in programmes.queryset])
        programmes.help_text = ('Each programme follows one schedule. Ticking a programme moves it here from its '
                                'other schedule.' if len(programmes.queryset) > 1 else '')
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

    READMISSION_DAYS = 30

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        earlier = self.patient.admissions.exclude(discharged_at=None).order_by('-discharged_at')
        field = self.fields['readmission_of']
        field.queryset = earlier
        field.label = 'Readmission of (earlier stay)'
        field.label_from_instance = lambda a: (f'{timezone.localtime(a.admitted_at):%d %b %Y} – '
                                               f'{timezone.localtime(a.discharged_at):%d %b %Y} · {a.reason}')
        field.help_text = (f'A subject can be admitted many times, one stay at a time. Filled in when the last stay '
                           f'ended within {self.READMISSION_DAYS} days; clear it if this admission is not related.')
        last = earlier.first()
        if not self.instance.pk and last and timezone.now() - last.discharged_at <= timedelta(days=self.READMISSION_DAYS):
            self.initial.setdefault('readmission_of', last.pk)


class DischargeForm(PatientChildForm):
    # Died in hospital: the death is recorded on the subject too (status, programmes, future visits).
    date_of_death = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}),
                                    help_text='Only when the outcome is Died.')
    cause_of_death = forms.CharField(max_length=255, required=False, help_text='Only when the outcome is Died.')

    class Meta:
        model = Admission
        fields = ['discharged_at', 'discharge_outcome', 'date_of_death', 'cause_of_death', 'discharge_summary']
        labels = {'discharged_at': 'Discharged / left hospital at'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['discharged_at'].required = True
        self.fields['discharge_outcome'].required = True
        if self.patient is not None and self.patient.is_deceased:
            self.initial.setdefault('date_of_death', self.patient.date_of_death)
            self.initial.setdefault('cause_of_death', self.patient.cause_of_death)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('discharge_outcome') == Admission.DischargeOutcome.DIED:
            died = cleaned.get('date_of_death')
            if not died:
                self.add_error('date_of_death', 'Give the date of death.')
            elif died > timezone.localdate():
                self.add_error('date_of_death', 'The date of death cannot be in the future.')
            elif died < timezone.localtime(self.instance.admitted_at).date():
                self.add_error('date_of_death', 'The date of death is before the admission.')
            if not cleaned.get('cause_of_death', '').strip():
                self.add_error('cause_of_death', 'Give the cause of death (or "Unknown").')
        return cleaned


class AppointmentStatusForm(forms.Form):
    status = forms.ChoiceField(choices=[('confirmed', 'Confirmed'), ('missed', 'Missed'), ('cancelled', 'Cancelled')])
    reason = forms.CharField(max_length=255, required=False)


# The clinics a clinic day can be for. The programme is the clinic (Diabetes Clinic, Cardiac Clinic, SCD Clinic).
CLINIC_CODES = ('dm-care', 'cardiac-care', 'scd-care')


def clinic_name(programme):
    return programme.name


class ClinicSessionForm(PatientChildForm):
    """One clinic at a time (Diabetes, Cardiac or SCD Clinic): the subjects of that programme are booked together."""

    clinic = forms.ModelChoiceField(queryset=None, empty_label='Choose the clinic…',
                                    help_text='The subjects of this programme are invited.')
    field_order = ['clinic']

    class Meta:
        model = ClinicSession
        fields = ['facility', 'start_date', 'end_date', 'start_time', 'include_ltfu', 'allocation', 'skip_weekends',
                  'move_existing', 'team', 'contact_phone', 'sms_template', 'send_without_consent']
        widgets = {'sms_template': forms.Textarea(attrs={'rows': 3})}
        help_texts = {'end_date': 'For a clinic over several days or weeks (optional). Dates can be changed later: '
                                  'booked appointments move with them.'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from clinical.models import ClinicalProgramme
        programmes = ClinicalProgramme.objects.filter(is_active=True, disease__is_active=True).select_related('disease')
        named = programmes.filter(code__in=CLINIC_CODES)
        clinic = self.fields['clinic']
        clinic.queryset = named if named.exists() else programmes
        clinic.label_from_instance = clinic_name
        if self.instance.pk:
            clinic.initial = self.instance.programmes.first()
            if self.instance.status != ClinicSession.Status.DRAFT:
                # The subjects are already booked for this clinic; only the dates and details change.
                clinic.disabled = True
                clinic.help_text = 'Fixed once the appointments are scheduled.'

    def save(self, commit=True):
        programme = self.cleaned_data['clinic']
        self.instance.name = clinic_name(programme)
        session = super().save(commit)
        if commit:
            session.programmes.set([programme])
        else:
            save_m2m = self.save_m2m

            def save_all():
                save_m2m()
                session.programmes.set([programme])
            self.save_m2m = save_all
        return session

    def clean_sms_template(self):
        template = self.cleaned_data['sms_template']
        try:
            template.format(facility='X', day='X', date='X', time='X', contact='X')
        except (KeyError, IndexError, ValueError) as exc:
            raise forms.ValidationError(f'Unknown placeholder {exc}. Use {{facility}} {{day}} {{date}} {{time}} {{contact}}.')
        return template


class ReferralForm(PatientChildForm):
    class Meta:
        model = Referral
        fields = ['facility', 'encounter', 'referral_date', 'to_facility', 'to_other', 'reason', 'notes',
                  'returned_on', 'feedback']
        widgets = {'notes': forms.Textarea(attrs={'rows': 2}), 'feedback': forms.Textarea(attrs={'rows': 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from facilities.models import Facility
        # Any active hospital, PEN-Plus or not (referral and mentoring hospitals included), except the subject's own.
        destination = self.fields['to_facility']
        destination.queryset = Facility.objects.filter(
            Q(is_active=True) | Q(pk=self.instance.to_facility_id)).exclude(
            pk=getattr(self.patient, 'current_facility_id', None)).order_by('-is_research_site', 'name')
        destination.help_text = 'The subject stays enrolled in the PEN-Plus clinic.'
        if not self.instance.pk:
            del self.fields['returned_on']
            del self.fields['feedback']

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get('to_facility') and not cleaned.get('to_other', '').strip():
            self.add_error('to_facility', 'Choose the hospital, or write it under "Other hospital".')
        returned = cleaned.get('returned_on')
        if returned and cleaned.get('referral_date') and returned < cleaned['referral_date']:
            self.add_error('returned_on', 'The return is before the referral.')
        return cleaned

