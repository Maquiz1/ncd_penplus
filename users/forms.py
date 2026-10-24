from django import forms
from django.contrib.auth.models import Group
from django.contrib.auth.forms import PasswordResetForm
from django.template.loader import render_to_string

from core.forms import BootstrapModelForm
from facilities.models import StaffAssignment
from facilities.scope import facilities_for_user

from .models import Position, User


class StaffCreateForm(BootstrapModelForm):
    groups = forms.ModelMultipleChoiceField(queryset=Group.objects.order_by('name'), required=False,
                                            widget=forms.CheckboxSelectMultiple, label='Roles')

    class Meta:
        model = User
        fields = ['username', 'first_name', 'last_name', 'email', 'phone', 'position', 'job_title', 'groups']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['position'].queryset = Position.objects.filter(is_active=True)
        self.fields['groups'].help_text = 'Leave empty to give the position\'s default role.'
        self.fields['email'].required = True
        self.fields['first_name'].required = True
        self.fields['last_name'].required = True

    def clean_email(self):
        email = self.cleaned_data['email'].lower()
        if User.objects.filter(email__iexact=email).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError('Another account already uses this email address.')
        return email

    def clean(self):
        cleaned = super().clean()
        position = cleaned.get('position')
        if not cleaned.get('groups') and position and position.default_role_id:
            cleaned['groups'] = Group.objects.filter(pk=position.default_role_id)
        return cleaned


class StaffUpdateForm(StaffCreateForm):
    class Meta(StaffCreateForm.Meta):
        fields = ['first_name', 'last_name', 'email', 'phone', 'position', 'job_title', 'groups']


class StaffAssignmentForm(BootstrapModelForm):
    class Meta:
        model = StaffAssignment
        fields = ['level', 'facility', 'district', 'region', 'zone', 'start_date', 'end_date']

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        from facilities.models import District, Facility, Region, Zone
        from facilities.scope import highest_level

        self.user = user
        facilities = facilities_for_user(user)
        self.fields['facility'].queryset = facilities
        self.fields['facility'].help_text = 'For facility-level assignments.'
        # Nobody can give a wider level than their own, nor an area outside their own scope.
        top = 'national' if user.is_superuser else highest_level(user)
        order = [value for value, _ in StaffAssignment.Level.choices]
        allowed = order[:order.index(top) + 1] if top else order[:1]
        self.fields['level'].choices = [c for c in StaffAssignment.Level.choices if c[0] in allowed]
        if top != 'national':  # only areas lying wholly inside the user's own scope
            outside = Facility.objects.exclude(pk__in=facilities)
            self.fields['district'].queryset = District.objects.filter(facilities__in=facilities).exclude(
                facilities__in=outside).distinct()
            self.fields['region'].queryset = Region.objects.filter(districts__facilities__in=facilities).exclude(
                districts__facilities__in=outside).distinct()
            self.fields['zone'].queryset = Zone.objects.filter(regions__districts__facilities__in=facilities).exclude(
                regions__districts__facilities__in=outside).distinct()
        for name in ('district', 'region', 'zone'):
            if name not in allowed:
                del self.fields[name]


class IdentityAccessForm(BootstrapModelForm):
    class Meta:
        model = User
        fields = ['identity_access', 'identity_access_reason', 'identity_access_until']
        widgets = {'identity_access_until': forms.DateInput(attrs={'type': 'date'})}
        labels = {'identity_access_reason': 'Reason (required for a grant or removal)'}

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('identity_access') != User.IdentityAccess.ROLE and not cleaned.get('identity_access_reason'):
            self.add_error('identity_access_reason', 'Give the reason, e.g. "Study monitor needs to verify source documents".')
        if cleaned.get('identity_access') == User.IdentityAccess.ROLE:
            cleaned['identity_access_until'] = None
        return cleaned


class LoggedPasswordResetForm(PasswordResetForm):
    """Password reset that sends through the system sender and records the email in the email log."""

    def send_mail(self, subject_template_name, email_template_name, context, from_email, to_email,
                  html_email_template_name=None):
        from notifications.email import send_logged_email

        subject = ''.join(render_to_string(subject_template_name, context).splitlines())
        body = render_to_string(email_template_name, context)
        html_body = render_to_string(html_email_template_name, context) if html_email_template_name else None
        # Never raise: the page must not reveal whether an address has an account.
        send_logged_email(to_email, subject, body, purpose='password_reset', user=context.get('user'),
                          html_body=html_body, fail_silently=True)
