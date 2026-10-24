"""System email: the active site address and sender, and a log of every message sent."""
from email.utils import formataddr, parseaddr

from django.conf import settings
from django.core.mail import EmailMultiAlternatives


def active_site():
    from .models import SiteConfiguration
    return SiteConfiguration.objects.filter(is_active=True).first()


def site_url():
    config = active_site()
    return (config.site_url if config else settings.SITE_URL).rstrip('/')


def from_email():
    """Active configuration's sender, falling back to DEFAULT_FROM_EMAIL for any part left blank."""
    config = active_site()
    default_name, default_address = parseaddr(settings.DEFAULT_FROM_EMAIL)
    name = (config and config.from_name) or default_name
    address = (config and config.from_email) or default_address
    return formataddr((name, address)) if name else address


def send_logged_email(recipient, subject, body, purpose='other', user=None, sent_by=None, html_body=None,
                      fail_silently=False):
    """Send one email and record it in the email log; re-raises delivery errors unless fail_silently."""
    from .models import EmailLog

    log = EmailLog(recipient=recipient, subject=subject[:255], purpose=purpose, user=user,
                   sent_by=sent_by if getattr(sent_by, 'pk', None) else None,
                   backend=settings.EMAIL_BACKEND.rsplit('.', 2)[-2] if '.' in settings.EMAIL_BACKEND else '')
    message = EmailMultiAlternatives(subject, body, from_email(), [recipient])
    if html_body:
        message.attach_alternative(html_body, 'text/html')
    try:
        message.send()
    except Exception as exc:  # noqa: BLE001 - every failure is logged for administrators
        log.status, log.error = EmailLog.Status.FAILED, str(exc)[:2000]
        log.save()
        if not fail_silently:
            raise
        return log
    log.status = EmailLog.Status.SENT
    log.save()
    return log
