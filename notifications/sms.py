"""SMS gateway adapters. The gateway and credentials come from Administration → SMS settings; anything left blank
there (or mode "Use the .env settings") falls back to .env. Credentials never live in code."""
import base64
import json
import logging
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass

from django.conf import settings

logger = logging.getLogger(__name__)


class SmsError(Exception):
    pass


def normalize_tz_phone(raw):
    """Return 2557XXXXXXXX / 2556XXXXXXXX, or '' if the number is not a valid Tanzanian mobile number."""
    digits = re.sub(r'\D', '', raw or '')
    if digits.startswith('0') and len(digits) == 10:
        digits = '255' + digits[1:]
    elif len(digits) == 9 and digits[0] in '67':
        digits = '255' + digits
    return digits if re.fullmatch(r'255[67]\d{8}', digits) else ''


def mask_phone(phone):
    return f'{phone[:5]}•••{phone[-3:]}' if phone else ''


@dataclass
class SmsConfig:
    backend: str            # console | beem | africastalking
    sender_id: str
    default_contact: str
    beem_api_key: str
    beem_secret_key: str
    at_username: str
    at_api_key: str
    nextsms_token: str
    nextsms_username: str
    nextsms_password: str
    source: str             # 'SMS settings' or '.env', for display


def sms_config():
    """Effective SMS settings: the SMS settings page first, .env for anything left blank."""
    from .models import SmsSettings
    from .secrets import decrypt

    page = SmsSettings.load()

    def secret(field, fallback, what):
        value = getattr(page, field)
        if not value:
            return fallback
        plain = decrypt(value)
        if plain is None:
            raise SmsError(f'The saved {what} cannot be read (the secret key changed?); enter it again in SMS settings.')
        return plain

    if page.mode == SmsSettings.Mode.ENV:
        backend, source = settings.SMS_BACKEND, '.env'
    else:
        backend, source = ('console' if page.mode == SmsSettings.Mode.CONSOLE else page.provider), 'SMS settings'
    return SmsConfig(
        backend=backend, source=source,
        sender_id=page.sender_id or settings.SMS_SENDER_ID,
        default_contact=page.default_contact or settings.SMS_DEFAULT_CONTACT,
        beem_api_key=secret('beem_api_key_encrypted', settings.BEEM_API_KEY, 'Beem API key'),
        beem_secret_key=secret('beem_secret_key_encrypted', settings.BEEM_SECRET_KEY, 'Beem secret key'),
        at_username=page.at_username or settings.AFRICASTALKING_USERNAME,
        at_api_key=secret('at_api_key_encrypted', settings.AFRICASTALKING_API_KEY, "Africa's Talking API key"),
        nextsms_token=secret('nextsms_token_encrypted', settings.NEXTSMS_API_TOKEN, 'NextSMS API token'),
        nextsms_username=page.nextsms_username or settings.NEXTSMS_USERNAME,
        nextsms_password=secret('nextsms_password_encrypted', settings.NEXTSMS_PASSWORD, 'NextSMS password'),
    )


class ConsoleBackend:
    """Development / no gateway yet: messages are logged and marked sent, nothing leaves the server."""

    name = 'console'

    def __init__(self, config):
        self.config = config

    def send(self, phone, body):
        logger.info('SMS (console) to %s: %s', mask_phone(phone), body)
        return 'console'


class BeemBackend(ConsoleBackend):
    name = 'beem'
    url = 'https://apisms.beem.africa/v1/send'

    def send(self, phone, body):
        c = self.config
        if not (c.beem_api_key and c.beem_secret_key and c.sender_id):
            raise SmsError('Beem is not configured: API key, secret key and sender ID are needed (SMS settings).')
        payload = json.dumps({'source_addr': c.sender_id, 'encoding': 0, 'message': body,
                              'recipients': [{'recipient_id': 1, 'dest_addr': phone}]}).encode()
        token = base64.b64encode(f'{c.beem_api_key}:{c.beem_secret_key}'.encode()).decode()
        request = urllib.request.Request(self.url, data=payload, method='POST', headers={
            'Content-Type': 'application/json', 'Authorization': f'Basic {token}'})
        data = _call(request)
        if not data.get('successful'):
            raise SmsError(str(data.get('message') or data)[:255])
        return str(data.get('request_id', ''))


class AfricasTalkingBackend(ConsoleBackend):
    name = 'africastalking'
    url = 'https://api.africastalking.com/version1/messaging'

    def send(self, phone, body):
        c = self.config
        if not (c.at_username and c.at_api_key):
            raise SmsError("Africa's Talking is not configured: username and API key are needed (SMS settings).")
        fields = {'username': c.at_username, 'to': f'+{phone}', 'message': body}
        if c.sender_id:
            fields['from'] = c.sender_id
        request = urllib.request.Request(self.url, data=urllib.parse.urlencode(fields).encode(), method='POST', headers={
            'apiKey': c.at_api_key, 'Accept': 'application/json',
            'Content-Type': 'application/x-www-form-urlencoded'})
        recipients = _call(request).get('SMSMessageData', {}).get('Recipients', [])
        if not recipients or recipients[0].get('status') != 'Success':
            raise SmsError(str(recipients[0].get('status') if recipients else 'No recipient accepted')[:255])
        return str(recipients[0].get('messageId', ''))


class NextSmsBackend(ConsoleBackend):
    """NextSMS (messaging-service.co.tz): v2 with an API token, or v1 with username and password."""

    name = 'nextsms'
    urls = {'v2': 'https://messaging-service.co.tz/api/sms/v2/text/single',
            'v1': 'https://messaging-service.co.tz/api/sms/v1/text/single'}
    failed_groups = {'REJECTED', 'FAILED', 'UNDELIVERABLE', 'EXPIRED'}

    def send(self, phone, body):
        c = self.config
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
        if c.nextsms_token:
            version, headers['Authorization'] = 'v2', f'Bearer {c.nextsms_token}'
        elif c.nextsms_username and c.nextsms_password:
            basic = base64.b64encode(f'{c.nextsms_username}:{c.nextsms_password}'.encode()).decode()
            version, headers['Authorization'] = 'v1', f'Basic {basic}'
        else:
            raise SmsError('NextSMS is not configured: an API token (or username and password) is needed (SMS settings).')
        if not c.sender_id:
            raise SmsError('NextSMS needs a sender ID (SMS settings).')
        payload = json.dumps({'from': c.sender_id, 'to': phone, 'text': body}).encode()
        data = _call(urllib.request.Request(self.urls[version], data=payload, method='POST', headers=headers))
        result = (data.get('messages') or [{}])[0]
        status = result.get('status') or {}
        if status.get('groupName') in self.failed_groups:
            raise SmsError(f"NextSMS {status.get('name') or status.get('groupName')}: "
                           f"{status.get('description', '')} (sender ID {c.sender_id})"[:255])
        return str(result.get('messageId', ''))


def _call(request):
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode() or '{}')
    except Exception as exc:  # noqa: BLE001 - surface gateway problems as delivery failures
        raise SmsError(str(exc)[:255]) from exc


BACKENDS = {cls.name: cls for cls in (ConsoleBackend, BeemBackend, AfricasTalkingBackend, NextSmsBackend)}


def get_backend(config=None):
    config = config or sms_config()
    try:
        return BACKENDS[config.backend](config)
    except KeyError:
        raise SmsError(f'Unknown SMS gateway "{config.backend}".')
