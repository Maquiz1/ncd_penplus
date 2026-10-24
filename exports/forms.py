from collections import OrderedDict

from django import forms

from core.forms import BootstrapForm
from facilities.models import District
from facilities.scope import facilities_for_user

from .datasets import all_datasets
from .models import ExportJob


def dataset_choices():
    groups = OrderedDict()
    for d in all_datasets():
        groups.setdefault(d.group, []).append((d.key, d.label))
    return [('all', 'All datasets (one Excel workbook, a sheet per dataset)')] + list(groups.items())


class ExportForm(BootstrapForm):
    dataset = forms.ChoiceField()
    fmt = forms.ChoiceField(label='Format', choices=ExportJob.Format.choices, initial='xlsx')
    mode = forms.ChoiceField(label='Answers as', choices=ExportJob.Mode.choices, initial='labels')
    district = forms.ModelChoiceField(queryset=District.objects.none(), required=False, empty_label='All districts')
    site = forms.ModelChoiceField(queryset=None, required=False, empty_label='All my sites')
    start = forms.DateField(required=False, label='Records from', widget=forms.DateInput(attrs={'type': 'date'}))
    end = forms.DateField(required=False, label='Records to', widget=forms.DateInput(attrs={'type': 'date'}))
    identifiable = forms.BooleanField(required=False, label='Include names, phone numbers and identifiers',
                                      help_text='Only for authorised data management. Every export is audited.')

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['dataset'].choices = dataset_choices()
        scope = facilities_for_user(user)
        self.fields['site'].queryset = scope.order_by('name')
        self.fields['district'].queryset = District.objects.filter(facilities__in=scope).distinct()
        if not (user.has_perm('exports.export_identifiable') and user.has_perm('patients.view_patient_identity')):
            del self.fields['identifiable']

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('dataset') == 'all':
            cleaned['fmt'] = 'xlsx'
        if cleaned.get('start') and cleaned.get('end') and cleaned['start'] > cleaned['end']:
            self.add_error('end', 'End date is before the start date.')
        return cleaned

    def facilities(self, user):
        scope = facilities_for_user(user)
        if self.cleaned_data.get('site'):
            return scope.filter(pk=self.cleaned_data['site'].pk)
        if self.cleaned_data.get('district'):
            return scope.filter(district=self.cleaned_data['district'])
        return None
