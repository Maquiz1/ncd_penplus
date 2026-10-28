from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from notifications.email import send_logged_email, site_url


def send_activation_email(user, sent_by=None):
    """Email the system URL and a single-use, time-limited set-password link.

    No password is ever generated or sent; the account has an unusable password
    until the staff member sets one through the link.
    """
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    path = reverse('password_reset_confirm', kwargs={'uidb64': uid, 'token': token})
    base_url = site_url()
    context = {
        'user': user,
        'site_name': settings.SITE_NAME,
        'site_url': base_url,
        'activation_url': f'{base_url}{path}',
        'valid_days': settings.PASSWORD_RESET_TIMEOUT // 86400,
    }
    send_logged_email(user.email, f'Your {settings.SITE_NAME} account',
                      render_to_string('users/activation_email.txt', context),
                      purpose='activation', user=user, sent_by=sent_by,
                      html_body=render_to_string('users/activation_email.html',
                                                 {**context, 'action_url': context['activation_url']}))


def pending_activation(users, migrated_only=False):
    """Active accounts with an email address that have never set a password."""
    from django.contrib.contenttypes.models import ContentType

    from integrations.models import ExternalRecord

    users = users.filter(is_active=True).exclude(email='').exclude(username='edcs_import')
    pending = [u for u in users.order_by('username') if not u.has_usable_password()]
    if migrated_only:
        ct = ContentType.objects.get_for_model(users.model)
        migrated = set(ExternalRecord.objects.filter(source='penplus_edcs', table='user', content_type=ct)
                       .values_list('object_id', flat=True))
        pending = [u for u in pending if str(u.pk) in migrated]
    return pending


def send_bulk_activation(users, actor=None, request=None):
    """Send activation links one by one; one bad address never stops the rest."""
    from django.core.exceptions import ValidationError
    from django.core.validators import validate_email

    from audit.models import AuditEvent
    from audit.services import log_event

    sent, failed = [], []
    for user in users:
        try:
            validate_email(user.email)
            send_activation_email(user, sent_by=actor)
        except ValidationError:
            failed.append((user, 'invalid email address'))
            continue
        except Exception as exc:  # noqa: BLE001 - report delivery problems per account
            failed.append((user, str(exc)[:200]))
            continue
        sent.append(user)
        log_event(actor=actor, request=request, action=AuditEvent.Action.ACCOUNT, obj=user,
                  reason='Activation link sent (bulk)')
    return sent, failed
