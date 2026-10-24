from django import forms

from clinical.models import Disease
from core.forms import BootstrapModelForm

from .models import Condition


class DiseaseForm(BootstrapModelForm):
    class Meta:
        model = Disease
        fields = ['code', 'name', 'description', 'order', 'is_active']
        help_texts = {'code': 'Short identifier without spaces, e.g. "epilepsy". Cannot be reused after deletion.',
                      'is_active': 'Inactive diseases are hidden from forms, indicators and reports (history kept).'}


class ConditionForm(BootstrapModelForm):
    class Meta:
        model = Condition
        fields = ['parent', 'code', 'name', 'indicator_code', 'keywords', 'is_default', 'excludes', 'order', 'is_active']
        widgets = {'excludes': forms.CheckboxSelectMultiple}

    def __init__(self, *args, disease=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.disease = disease
        self.fields['excludes'].queryset = Condition.objects.filter(disease=disease, parent__isnull=True).exclude(pk=self.instance.pk)
        self.fields['parent'].queryset = Condition.objects.filter(disease=disease, parent__isnull=True).exclude(pk=self.instance.pk)
        self.fields['parent'].label = 'Sub-type of'

    def clean_is_default(self):
        value = self.cleaned_data['is_default']
        if value and Condition.objects.filter(disease=self.disease, is_default=True).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError('Another condition is already the default for this disease.')
        return value
