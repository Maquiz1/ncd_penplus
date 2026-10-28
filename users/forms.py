import re

from django import forms
from django.contrib.auth.forms import PasswordResetForm, SetPasswordForm
from django.contrib.auth.models import Group
from django.template.loader import render_to_string

from core.forms import BootstrapModelForm, apply_bootstrap
from facilities.models import StaffAssignment
from facilities.scope import facilities_for_user

from .models import Position, User


class StaffCreateForm(BootstrapModelForm):
    groups = forms.ModelMultipleChoiceField(queryset=Group.objects.order_by('name'), required=False,
                                            widget=forms.CheckboxSelectMultiple, label='Roles')
    diseases = forms.ModelMultipleChoiceField(queryset=None, required=False, widget=forms.CheckboxSelectMultiple,
                                              help_text='Programmes this person works on, e.g. only Cardiac for a cardiologist. '
                                                        'They then see only those patients, indicators and reports. '
                                                        'Leave empty for all diseases (coordinators, PIs, data managers).')

    class Meta:
        model = User
        fields = ['username', 'prefix', 'first_name', 'last_name', 'email', 'phone', 'position', 'job_title', 'groups',
                  'diseases']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['position'].queryset = Position.objects.filter(is_active=True)
        from clinical.models import Disease
        self.fields['diseases'].queryset = Disease.objects.filter(is_active=True)
        self.fields['groups'].help_text = 'Leave empty to give the position\'s default role.'
        self.fields['email'].required = False
        self.fields['email'].help_text = ('With an email, the person gets an activation link and can use "Forgot your '
                                          'password?". Without one, an administrator sets the password.')
        if 'username' in self.fields:
            self.fields['username'].required = False
            self.fields['username'].help_text = ('Used to log in (the email works too). Leave empty to make it from the '
                                                 'email address.')
        self.fields['first_name'].required = True
        self.fields['last_name'].required = True

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        if email and User.objects.filter(email__iexact=email).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError('Another account already uses this email address.')
        return email

    def clean(self):
        cleaned = super().clean()
        if 'username' in self.fields and not cleaned.get('username') and 'username' not in self.errors:
            if cleaned.get('email'):
                cleaned['username'] = unique_username(cleaned['email'].split('@')[0])
            elif 'email' not in self.errors:
                raise forms.ValidationError('Give a username, an email address, or both.')
        position = cleaned.get('position')
        if not cleaned.get('groups') and position and position.default_role_id:
            cleaned['groups'] = Group.objects.filter(pk=position.default_role_id)
        return cleaned


def unique_username(base):
    """A free username made from the start of an email address, e.g. asha.mushi, asha.mushi2."""
    base = re.sub(r'[^\w.@+-]', '', base)[:140] or 'staff'
    username, n = base, 1
    while User.objects.filter(username__iexact=username).exists():
        n += 1
        username = f'{base}{n}'
    return username


class StaffSetPasswordForm(SetPasswordForm):
    """Administrator sets a first or new password; the person must change it at the next login."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_bootstrap(self.fields)
        self.fields['new_password1'].help_text = ('Give it to the person privately. They must choose their own '
                                                  'password at the next login.')


class StaffUpdateForm(StaffCreateForm):
    class Meta(StaffCreateForm.Meta):
        fields = ['prefix', 'first_name', 'last_name', 'email', 'phone', 'position', 'job_title', 'groups', 'diseases']


class ParentSelect(forms.Select):
    """Select whose options carry their parent's id (data-parent), so the page can narrow the list when the
    zone, region or district above it is chosen."""

    def __init__(self, parent_field, *args, **kwargs):
        self.parent_field = parent_field
        super().__init__(*args, **kwargs)

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        instance = getattr(value, 'instance', None)
        if instance is not None:
            option['attrs']['data-parent'] = getattr(instance, f'{self.parent_field}_id')
        return option


class StaffAssignmentForm(BootstrapModelForm):
    class Meta:
        model = StaffAssignment
        # Top-down, so zone -> region -> district -> facility narrow each other on the page.
        fields = ['level', 'zone', 'region', 'district', 'facility', 'start_date', 'end_date']
        widgets = {'region': ParentSelect('zone'), 'district': ParentSelect('region'),
                   'facility': ParentSelect('district')}

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        from facilities.models import District, Facility, Region, Zone
        from facilities.scope import highest_level

        self.user = user
        facilities = facilities_for_user(user)
        self.fields['facility'].queryset = facilities
        self.fields['facility'].help_text = 'Choose the district (or region, zone) first to shorten this list.'
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
    """Password reset that sends through the system sender and records the email in the email log.

    Unlike Django's default it also reaches active accounts that never set a password, so a staff member whose
    activation link expired can get a new one themselves."""

    def get_users(self, email):
        return User._default_manager.filter(email__iexact=email.strip(), is_active=True).exclude(email='')

    def send_mail(self, subject_template_name, email_template_name, context, from_email, to_email,
                  html_email_template_name=None):
        from notifications.email import send_logged_email

        subject = ''.join(render_to_string(subject_template_name, context).splitlines())
        body = render_to_string(email_template_name, context)
        html_body = render_to_string(html_email_template_name, context) if html_email_template_name else None
        # Never raise: the page must not reveal whether an address has an account.
        send_logged_email(to_email, subject, body, purpose='password_reset', user=context.get('user'),
                          html_body=html_body, fail_silently=True)
