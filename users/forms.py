from django import forms
from django.contrib.auth.models import Group

from core.forms import BootstrapModelForm
from facilities.models import StaffAssignment
from facilities.scope import facilities_for_user

from .models import User


class StaffCreateForm(BootstrapModelForm):
    groups = forms.ModelMultipleChoiceField(queryset=Group.objects.order_by('name'), required=False,
                                            widget=forms.CheckboxSelectMultiple, label='Roles')

    class Meta:
        model = User
        fields = ['username', 'first_name', 'last_name', 'email', 'phone', 'job_title', 'groups']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['email'].required = True
        self.fields['first_name'].required = True
        self.fields['last_name'].required = True

    def clean_email(self):
        email = self.cleaned_data['email'].lower()
        if User.objects.filter(email__iexact=email).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError('Another account already uses this email address.')
        return email


class StaffUpdateForm(StaffCreateForm):
    class Meta(StaffCreateForm.Meta):
        fields = ['first_name', 'last_name', 'email', 'phone', 'job_title', 'groups']


class StaffAssignmentForm(BootstrapModelForm):
    class Meta:
        model = StaffAssignment
        fields = ['level', 'facility', 'district', 'region', 'zone', 'start_date', 'end_date']

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['facility'].queryset = facilities_for_user(user)
        self.fields['facility'].help_text = 'For facility-level assignments.'
        if not user.is_superuser:
            # Only system administrators can grant wider than facility scope.
            self.fields['level'].choices = [(StaffAssignment.Level.FACILITY, 'Facility')]
            for name in ('district', 'region', 'zone'):
                del self.fields[name]
