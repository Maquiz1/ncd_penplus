from django import forms

from core.forms import BootstrapForm, BootstrapModelForm, PatientChildForm, apply_bootstrap

from .models import LabCategory, LabOrder, LabPanel, LabTest, MonitoringGap


class LabOrderForm(PatientChildForm):
    panels = forms.ModelMultipleChoiceField(queryset=LabPanel.objects.filter(is_active=True), required=False,
                                            widget=forms.CheckboxSelectMultiple, help_text='Orders every test in the panel.')
    tests = forms.ModelMultipleChoiceField(queryset=LabTest.objects.filter(is_active=True).select_related('category'),
                                           required=False, widget=forms.CheckboxSelectMultiple,
                                           label='Individual tests')

    class Meta:
        model = LabOrder
        fields = ['panels', 'tests', 'facility', 'encounter', 'priority', 'clinical_notes']

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get('panels') and not cleaned.get('tests'):
            raise forms.ValidationError('Select at least one panel or test.')
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


class SpecimenForm(BootstrapForm):
    specimen_id = forms.CharField(max_length=50, label='Specimen / barcode ID')
    collected_at = forms.DateTimeField()


class GroupResultForm(BootstrapForm):
    """One field per collected test in the request group, typed by the test's result type."""

    result_notes = forms.CharField(required=False, widget=forms.Textarea, label='Notes')

    def __init__(self, *args, orders=(), **kwargs):
        self.orders = list(orders)
        super().__init__(*args, **kwargs)
        for order in self.orders:
            test = order.test
            label = f'{test.name}' + (f' ({test.unit})' if test.unit else '')
            help_text = f'Reference: {test.reference_display}' if test.reference_display else ''
            if test.result_type == LabTest.ResultType.NUMERIC:
                field = forms.DecimalField(label=label, required=False, help_text=help_text)
            elif test.result_type == LabTest.ResultType.CHOICE:
                field = forms.ChoiceField(label=label, required=False, help_text=help_text,
                                          choices=[('', '---------')] + [(c, c) for c in test.choice_list()])
            else:
                field = forms.CharField(label=label, required=False, max_length=100, help_text=help_text)
            self.fields[f'r{order.pk}'] = field
        self.order_fields([f'r{o.pk}' for o in self.orders] + ['result_notes'])
        apply_bootstrap(self.fields)

    def clean(self):
        cleaned = super().clean()
        if not any(cleaned.get(f'r{o.pk}') not in (None, '') for o in self.orders):
            raise forms.ValidationError('Enter at least one result.')
        return cleaned

    def results(self):
        for order in self.orders:
            value = self.cleaned_data.get(f'r{order.pk}')
            if value not in (None, ''):
                yield order, (f'{value.normalize():f}' if hasattr(value, 'normalize') else str(value))


class LabCategoryForm(BootstrapModelForm):
    class Meta:
        model = LabCategory
        fields = ['name', 'order']


class LabTestForm(BootstrapModelForm):
    apply_unit_to_existing = forms.BooleanField(
        required=False, label='Also relabel existing results that were recorded with the old unit',
        help_text='Use only to correct a wrong unit label. Values are not converted.')

    class Meta:
        model = LabTest
        fields = ['code', 'name', 'category', 'specimen_type', 'result_type', 'unit', 'reference_low',
                  'reference_high', 'reference_range', 'choices', 'order', 'is_active']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            del self.fields['apply_unit_to_existing']


class LabPanelForm(BootstrapModelForm):
    class Meta:
        model = LabPanel
        fields = ['code', 'name', 'tests', 'is_active']
        widgets = {'tests': forms.CheckboxSelectMultiple}


class MonitoringGapForm(BootstrapModelForm):
    class Meta:
        model = MonitoringGap
        fields = ['reason', 'note']
        labels = {'reason': 'Why was it not done?'}
        help_texts = {'note': 'Optional, e.g. "kits expected next week".'}
