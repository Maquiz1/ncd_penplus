"""SMS gateway adapters. The backend is chosen by SMS_BACKEND in .env; credentials never live in code."""
import base64
import json
import logging
import re
import urllib.parse
import urllib.request

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


class ConsoleBackend:
    """Development / no gateway yet: messages are logged and marked sent, nothing leaves the server."""

    name = 'console'

    def send(self, phone, body):
        logger.info('SMS (console) to %s: %s', mask_phone(phone), body)
        return 'console'


class BeemBackend:
    name = 'beem'
    url = 'https://apisms.beem.africa/v1/send'

    def send(self, phone, body):
        if not (settings.BEEM_API_KEY and settings.BEEM_SECRET_KEY and settings.SMS_SENDER_ID):
            raise SmsError('Beem is not configured (BEEM_API_KEY, BEEM_SECRET_KEY, SMS_SENDER_ID).')
        payload = json.dumps({'source_addr': settings.SMS_SENDER_ID, 'encoding': 0, 'message': body,
                              'recipients': [{'recipient_id': 1, 'dest_addr': phone}]}).encode()
        token = base64.b64encode(f'{settings.BEEM_API_KEY}:{settings.BEEM_SECRET_KEY}'.encode()).decode()
        request = urllib.request.Request(self.url, data=payload, method='POST', headers={
            'Content-Type': 'application/json', 'Authorization': f'Basic {token}'})
        data = _call(request)
        if not data.get('successful'):
            raise SmsError(str(data.get('message') or data)[:255])
        return str(data.get('request_id', ''))


class AfricasTalkingBackend:
    name = 'africastalking'
    url = 'https://api.africastalking.com/version1/messaging'

    def send(self, phone, body):
        if not (settings.AFRICASTALKING_USERNAME and settings.AFRICASTALKING_API_KEY):
            raise SmsError("Africa's Talking is not configured (AFRICASTALKING_USERNAME, AFRICASTALKING_API_KEY).")
        fields = {'username': settings.AFRICASTALKING_USERNAME, 'to': f'+{phone}', 'message': body}
        if settings.SMS_SENDER_ID:
            fields['from'] = settings.SMS_SENDER_ID
        request = urllib.request.Request(self.url, data=urllib.parse.urlencode(fields).encode(), method='POST', headers={
            'apiKey': settings.AFRICASTALKING_API_KEY, 'Accept': 'application/json',
            'Content-Type': 'application/x-www-form-urlencoded'})
        recipients = _call(request).get('SMSMessageData', {}).get('Recipients', [])
        if not recipients or recipients[0].get('status') != 'Success':
            raise SmsError(str(recipients[0].get('status') if recipients else 'No recipient accepted')[:255])
        return str(recipients[0].get('messageId', ''))


def _call(request):
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode() or '{}')
    except Exception as exc:  # noqa: BLE001 - surface gateway problems as delivery failures
        raise SmsError(str(exc)[:255]) from exc


BACKENDS = {cls.name: cls for cls in (ConsoleBackend, BeemBackend, AfricasTalkingBackend)}


def get_backend():
    try:
        return BACKENDS[settings.SMS_BACKEND]()
    except KeyError:
        raise SmsError(f'Unknown SMS_BACKEND "{settings.SMS_BACKEND}".')
