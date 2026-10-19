from django import forms

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


class ReasonForm(BootstrapForm):
    reason = forms.CharField(max_length=255, widget=forms.Textarea(attrs={'rows': 2}))


class DateRangeForm(BootstrapForm):
    start = forms.DateField(required=False, label='From')
    end = forms.DateField(required=False, label='To')
