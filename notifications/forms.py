from django import forms

from core.forms import BootstrapForm, BootstrapModelForm

from .models import SmsSettings
from .secrets import encrypt

SECRETS = {'beem_api_key': ('beem_api_key_encrypted', 'Beem API key'),
           'beem_secret_key': ('beem_secret_key_encrypted', 'Beem secret key'),
           'at_api_key': ('at_api_key_encrypted', "Africa's Talking API key"),
           'nextsms_token': ('nextsms_token_encrypted', 'NextSMS API token'),
           'nextsms_password': ('nextsms_password_encrypted', 'NextSMS password')}


class SmsSettingsForm(BootstrapModelForm):
    """Secrets are write-only: a blank box keeps the saved value; tick "clear" to remove it."""

    beem_api_key = forms.CharField(label='Beem API key', required=False, widget=forms.PasswordInput)
    beem_secret_key = forms.CharField(label='Beem secret key', required=False, widget=forms.PasswordInput)
    at_api_key = forms.CharField(label="Africa's Talking API key", required=False, widget=forms.PasswordInput)
    nextsms_token = forms.CharField(label='NextSMS API token', required=False, widget=forms.PasswordInput)
    nextsms_password = forms.CharField(label='NextSMS password', required=False, widget=forms.PasswordInput,
                                       help_text='Only for older accounts without an API token.')
    clear_secrets = forms.BooleanField(label='Clear the saved keys (use .env instead)', required=False)

    field_order = ['mode', 'provider', 'sender_id', 'default_contact', 'beem_api_key', 'beem_secret_key', 'at_username',
                   'at_api_key', 'nextsms_token', 'nextsms_username', 'nextsms_password', 'clear_secrets']

    class Meta:
        model = SmsSettings
        fields = ['mode', 'provider', 'sender_id', 'default_contact', 'at_username', 'nextsms_username']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['mode'].widget = forms.RadioSelect(choices=SmsSettings.Mode.choices)
        self.fields['mode'].widget.attrs['class'] = 'form-check-input'
        for name, (field, label) in SECRETS.items():
            saved = bool(getattr(self.instance, field))
            self.fields[name].help_text = ('Saved (hidden). Leave blank to keep it.' if saved
                                           else 'Not saved: .env is used if set there.')

    def save(self, commit=True):
        settings_row = super().save(commit=False)
        if self.cleaned_data.get('clear_secrets'):
            for field, _ in SECRETS.values():
                setattr(settings_row, field, '')
        for name, (field, _) in SECRETS.items():
            if self.cleaned_data.get(name):
                setattr(settings_row, field, encrypt(self.cleaned_data[name].strip()))
        if commit:
            settings_row.save()
        return settings_row


class TestSmsForm(BootstrapForm):
    phone = forms.CharField(max_length=20, help_text='Your own mobile number, e.g. 0712 345 678.')
    message = forms.CharField(max_length=160, initial='Test message from NCD - PENPLUS STUDY.',
                              widget=forms.Textarea(attrs={'rows': 2}))

    def clean_phone(self):
        from .sms import normalize_tz_phone
        phone = normalize_tz_phone(self.cleaned_data['phone'])
        if not phone:
            raise forms.ValidationError('Enter a valid Tanzanian mobile number.')
        return phone
