from django import forms

from core.forms import BootstrapModelForm, PatientChildForm, apply_bootstrap
from laboratory.models import LabOrder, LabPanel

from .models import InvestigationField, InvestigationOrder, InvestigationType
from .ordering import history, orderable_tests

RESULT_PREFIX = 'r_'


class InvestigationRequestForm(PatientChildForm):
    """One request from a visit: laboratory tests and panels, and radiology. Each person only gets what they may order."""

    panels = forms.ModelMultipleChoiceField(queryset=LabPanel.objects.filter(is_active=True), required=False,
                                            widget=forms.CheckboxSelectMultiple)
    tests = forms.ModelMultipleChoiceField(queryset=orderable_tests(), required=False,
                                           widget=forms.CheckboxSelectMultiple)
    types = forms.ModelMultipleChoiceField(queryset=InvestigationType.objects.filter(is_active=True), required=False,
                                           widget=forms.CheckboxSelectMultiple)
    scheduled_for = forms.DateTimeField(required=False, label='Radiology appointment')
    confirm_repeat = forms.BooleanField(required=False, label='Yes, request these again')

    class Meta:
        model = LabOrder
        fields = ['facility', 'encounter', 'priority', 'clinical_notes']
        labels = {'priority': 'Laboratory priority', 'clinical_notes': 'Clinical notes / reason for request',
                  'encounter': 'Visit'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.repeats = []
        self.notes = history(self.patient)
        self.fields['priority'].required = False
        if not self.user.has_perm('laboratory.add_laborder'):
            self.fields['panels'].queryset = LabPanel.objects.none()
            self.fields['tests'].queryset = orderable_tests().none()
        if not self.user.has_perm('investigations.add_investigationorder'):
            self.fields['types'].queryset = InvestigationType.objects.none()

    def clean(self):
        cleaned = super().clean()
        tests, types = self.requested_tests(), cleaned.get('types') or []
        if not tests and not types:
            raise forms.ValidationError('Select at least one test.')
        if any(t.code == 'other-procedure' for t in types) and not cleaned.get('clinical_notes'):
            self.add_error('clinical_notes', 'Describe the procedure you are requesting.')
        self.repeats = ([t.name for t, _panel in tests if self.notes['test'].get(t.pk, ('', False))[1]]
                        + [t.name for t in types if self.notes['type'].get(t.pk, ('', False))[1]])
        if self.repeats and not cleaned.get('confirm_repeat'):
            raise forms.ValidationError('Already requested and not finished, or requested today: '
                                        f'{", ".join(self.repeats)}.')
        cleaned['priority'] = cleaned.get('priority') or LabOrder.Priority.ROUTINE
        return cleaned

    def requested_tests(self):
        """(test, panel) pairs without duplicates; panel tests first."""
        seen, result = set(), []
        for panel in self.cleaned_data.get('panels') or []:
            for test in panel.tests.filter(is_active=True):
                if test.pk not in seen:
                    seen.add(test.pk)
                    result.append((test, panel))
        for test in self.cleaned_data.get('tests') or []:
            if test.pk not in seen:
                seen.add(test.pk)
                result.append((test, None))
        return result


class InvestigationReportForm(PatientChildForm):
    """Report with the type's structured fields (EF %, rhythm, RHD criteria …) followed by free-text findings."""

    class Meta:
        model = InvestigationOrder
        fields = ['performed_at', 'findings', 'conclusion', 'is_abnormal', 'attachment']
        help_texts = {'is_abnormal': 'Left blank, it is set from the structured results (abnormal options / out of range).'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['performed_at'].required = True
        self.result_fields = []
        itype = self.instance.investigation_type
        if itype is not None:
            for f in itype.fields.filter(is_active=True):
                name = RESULT_PREFIX + f.key
                label = f'{f.label} ({f.unit})' if f.unit else f.label
                initial = self.instance.results.get(f.key, '')
                if f.field_type == InvestigationField.FieldType.CHOICE:
                    field = forms.ChoiceField(label=label, required=f.required, initial=initial,
                                              choices=[('', '—')] + [(c, c) for c in f.choice_list()])
                elif f.field_type == InvestigationField.FieldType.NUMBER:
                    field = forms.DecimalField(label=label, required=f.required, initial=initial or None,
                                               decimal_places=2, max_digits=10)
                else:
                    field = forms.CharField(label=label, required=f.required, initial=initial, max_length=255)
                self.fields[name] = field
                self.result_fields.append((name, f))
            # Structured fields first, then the free-text report.
            self.order_fields([n for n, _ in self.result_fields] + list(self.Meta.fields))
            apply_bootstrap(self.fields)
        self.instance.status = InvestigationOrder.Status.COMPLETED

    def clean(self):
        cleaned = super().clean()
        results, abnormal = {}, False
        for name, f in self.result_fields:
            value = cleaned.get(name)
            if value not in (None, ''):
                value = f'{value.normalize():f}' if f.field_type == InvestigationField.FieldType.NUMBER else str(value)
                results[f.key] = value
                abnormal = abnormal or f.is_abnormal(value)
        self.instance.results = results
        if cleaned.get('is_abnormal') is None and results:
            cleaned['is_abnormal'] = abnormal
        if not (results or cleaned.get('findings') or cleaned.get('attachment') or self.instance.attachment):
            raise forms.ValidationError('Enter the results or findings, or attach the report.')
        return cleaned


class InvestigationTypeForm(BootstrapModelForm):
    class Meta:
        model = InvestigationType
        fields = ['code', 'name', 'modality', 'description', 'preparation', 'order', 'is_active']


class InvestigationFieldForm(BootstrapModelForm):
    class Meta:
        model = InvestigationField
        fields = ['label', 'key', 'field_type', 'unit', 'reference_low', 'reference_high', 'choices', 'required',
                  'order', 'is_active']
        widgets = {'choices': forms.Textarea(attrs={'rows': 4})}

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('field_type') == InvestigationField.FieldType.CHOICE and not (cleaned.get('choices') or '').strip():
            self.add_error('choices', 'Add at least one option.')
        return cleaned
