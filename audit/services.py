from django.db import models

from .models import AuditEvent

EXCLUDED_FIELDS = {'updated_at', 'password', 'last_login'}


def snapshot(instance):
    """Serialize concrete field values so changes can be compared."""
    data = {}
    for field in instance._meta.concrete_fields:
        if field.name in EXCLUDED_FIELDS or isinstance(field, models.FileField):
            continue
        data[field.name] = field.value_to_string(instance)
    return data


def diff(before, after):
    return {key: [before.get(key), value] for key, value in after.items() if before.get(key) != value}


def _client_ip(request):
    if request is None:
        return None
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    return (forwarded.split(',')[0].strip() if forwarded else request.META.get('REMOTE_ADDR')) or None


def log_event(actor=None, action='', obj=None, changes=None, reason='', request=None, model='', object_repr=''):
    if request is not None and actor is None and request.user.is_authenticated:
        actor = request.user
    if actor is not None and not getattr(actor, 'is_authenticated', False):
        actor = None
    return AuditEvent.objects.create(
        actor=actor,
        action=action,
        model=obj._meta.label if obj is not None else model,
        object_id=str(obj.pk) if obj is not None else '',
        object_repr=(str(obj) if obj is not None else object_repr)[:255],
        changes=changes or {},
        reason=(reason or '')[:255],
        ip_address=_client_ip(request),
    )
