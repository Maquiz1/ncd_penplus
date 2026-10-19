from django import forms

from core.forms import BootstrapModelForm
from core.models import LocationType, WorkflowStage

from .models import FormDefinition, FormField, FormSection


class FormDefinitionForm(BootstrapModelForm):
    stages = forms.MultipleChoiceField(choices=WorkflowStage.choices, required=False,
                                       widget=forms.CheckboxSelectMultiple,
                                       help_text='Leave empty to allow the form at any stage.')
    locations = forms.MultipleChoiceField(choices=LocationType.choices, required=False,
                                          widget=forms.CheckboxSelectMultiple,
                                          help_text='Leave empty for facility encounters only.')

    class Meta:
        model = FormDefinition
        fields = ['code', 'name', 'description', 'applicability', 'diseases', 'study', 'stages', 'locations',
                  'is_required', 'is_repeatable', 'is_active']
        widgets = {'diseases': forms.CheckboxSelectMultiple}

    def clean(self):
        cleaned = super().clean()
        applicability = cleaned.get('applicability')
        diseases = cleaned.get('diseases')
        if applicability == 'single' and (not diseases or len(diseases) != 1):
            self.add_error('diseases', 'Select exactly one disease for a single-disease form.')
        if applicability == 'selected' and (not diseases or len(diseases) < 2):
            self.add_error('diseases', 'Select two or more diseases for a selected-disease form.')
        if applicability == 'study' and not cleaned.get('study'):
            self.add_error('study', 'Select the study this form belongs to.')
        if applicability == 'universal':
            cleaned['diseases'] = []
        return cleaned


class FormSectionForm(BootstrapModelForm):
    class Meta:
        model = FormSection
        fields = ['title', 'order', 'help_text']


class FormFieldForm(BootstrapModelForm):
    class Meta:
        model = FormField
        fields = ['section', 'order', 'key', 'label', 'field_type', 'required', 'options', 'unit', 'min_value',
                  'max_value', 'help_text', 'show_if_key', 'show_if_value']

    def __init__(self, *args, version=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['section'].queryset = version.sections.all()


class SubmissionReviewForm(forms.Form):
    decision = forms.ChoiceField(choices=[('verified', 'Verify'), ('returned', 'Return for correction'),
                                          ('under_review', 'Mark under review')])
    comment = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 2, 'class': 'form-control'}))
