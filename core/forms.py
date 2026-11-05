from django import forms
from django.utils import timezone

from facilities.scope import facilities_for_user


def apply_bootstrap(fields):
    """Give every field a Bootstrap 5 widget class and HTML5 date/time inputs."""
    for field in fields.values():
        if getattr(field, '_bootstrapped', False):
            continue
        field._bootstrapped = True
        if isinstance(field, forms.DateTimeField):
            field.widget = forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M')
        elif isinstance(field, forms.DateField):
            field.widget = forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d')
        elif isinstance(field, forms.TimeField):
            field.widget = forms.TimeInput(attrs={'type': 'time'}, format='%H:%M')
        widget = field.widget
        if isinstance(widget, (forms.CheckboxInput, forms.CheckboxSelectMultiple, forms.RadioSelect)):
            css = 'form-check-input'
        elif isinstance(widget, (forms.Select, forms.SelectMultiple)):
            css = 'form-select'
        else:
            css = 'form-control'
        if isinstance(widget, forms.Textarea):
            widget.attrs.setdefault('rows', 3)
        widget.attrs['class'] = f"{widget.attrs.get('class', '')} {css}".strip()


class BootstrapFormMixin:
    """Apply Bootstrap 5 classes and HTML5 date inputs to every field."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_bootstrap(self.fields)

    def full_clean(self):
        super().full_clean()
        for name in self.errors:
            if name in self.fields:
                widget = self.fields[name].widget
                widget.attrs['class'] = f"{widget.attrs.get('class', '')} is-invalid".strip()


class BootstrapForm(BootstrapFormMixin, forms.Form):
    pass


class BootstrapModelForm(BootstrapFormMixin, forms.ModelForm):
    pass


class PatientChildForm(BootstrapModelForm):
    """Form for a record that belongs to a patient.

    Limits related-record choices to the given patient and facility choices to
    the user's access scope, so users cannot link records they cannot see.
    """

    def __init__(self, *args, patient=None, user=None, **kwargs):
        self.patient = patient
        self.user = user
        self.locked_visit = None
        super().__init__(*args, **kwargs)
        if user is not None:
            for name in ('facility',):
                if name in self.fields:
                    self.fields[name].queryset = facilities_for_user(user)
        if patient is not None:
            related = {
                'encounter': patient.encounters.all(),
                'appointment': patient.appointments.all(),
                'programme_enrollment': patient.programme_enrollments.all(),
                'study_enrollment': patient.study_enrollments.all(),
            }
            for name, queryset in related.items():
                if name in self.fields:
                    self.fields[name].queryset = queryset
            encounter = self.fields.get('encounter')
            visit = self.initial.get('encounter') or (self.instance.encounter_id if hasattr(self.instance, 'encounter_id') else None)
            if encounter is not None and str(visit or '').isdigit() and encounter.queryset.filter(pk=visit).exists():
                # Opened from a visit (or the record already belongs to one): it is linked to that visit, and to the
                # visit's facility, only.
                encounter.queryset = encounter.queryset.filter(pk=visit)
                self.locked_visit = encounter.queryset.first()
                encounter.empty_label = None
                encounter.required = False
                encounter.help_text = ''
                facility = self.fields.get('facility')
                if facility is not None:
                    facility.queryset = facility.queryset.model.objects.filter(pk=self.locked_visit.facility_id)
                    facility.empty_label = None
                    facility.help_text = 'The facility of this visit.'
                    self.initial['facility'] = self.locked_visit.facility_id
            if encounter is not None:
                encounter.label = 'Visit'
                encounter.label_from_instance = lambda v: (f'{v.get_stage_display()} visit · '
                                                           f'{timezone.localtime(v.encounter_date):%d %b %Y}')
            if self.locked_visit is None and encounter is not None and not encounter.required:
                encounter.empty_label = 'Not linked to a visit'
                encounter.help_text = encounter.help_text or 'Optional. Link it to a visit (encounter) if one was recorded.'

    def clean(self):
        cleaned = super().clean()
        visit = self.locked_visit
        if visit is not None:
            cleaned['encounter'] = visit
            if 'facility' in self.fields:
                cleaned['facility'] = visit.facility
        return cleaned


class ReasonForm(BootstrapForm):
    reason = forms.CharField(max_length=255, widget=forms.Textarea(attrs={'rows': 2}))


class ReviewForm(BootstrapForm):
    comment = forms.CharField(label='Doctor comment', required=False, widget=forms.Textarea(attrs={'rows': 3}),
                              help_text='Optional: your interpretation or what should happen next.')


class DateRangeForm(BootstrapForm):
    start = forms.DateField(required=False, label='From')
    end = forms.DateField(required=False, label='To')