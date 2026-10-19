from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from .models import AuditEvent
from .services import log_event


@receiver(user_logged_in)
def on_login(sender, request, user, **kwargs):
    log_event(actor=user, action=AuditEvent.Action.LOGIN, obj=user, request=request)


@receiver(user_logged_out)
def on_logout(sender, request, user, **kwargs):
    if user is not None:
        log_event(actor=user, action=AuditEvent.Action.LOGOUT, obj=user, request=request)


@receiver(user_login_failed)
def on_login_failed(sender, credentials, request=None, **kwargs):
    # Never record the attempted password.
    log_event(action=AuditEvent.Action.LOGIN_FAILED, request=request, model='users.User',
              object_repr=str(credentials.get('username', ''))[:150])
