"""Permission rules on top of roles: per-person identity access and national-only system setup."""
from fnmatch import fnmatch

from django.contrib.auth.backends import ModelBackend

from core.roles import NATIONAL_ONLY

IDENTITY_PERM = 'patients.view_patient_identity'


def has_national_scope(user):
    from facilities.scope import effective_assignments
    return user.is_superuser or effective_assignments(user).filter(level='national').exists()


class AccessBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        """Log in with the username or, when it belongs to exactly one account, the email address."""
        if username and '@' in username:
            from django.contrib.auth import get_user_model
            matches = list(get_user_model()._default_manager.filter(email__iexact=username.strip(),
                                                                    is_deleted=False)[:2])
            if len(matches) == 1:
                username = matches[0].get_username()
        return super().authenticate(request, username=username, password=password, **kwargs)

    def get_all_permissions(self, user_obj, obj=None):
        if not user_obj.is_active or user_obj.is_anonymous or obj is not None:
            return super().get_all_permissions(user_obj, obj)
        if not hasattr(user_obj, '_access_perm_cache'):
            perms = set(super().get_all_permissions(user_obj, obj))
            override = user_obj.identity_override
            if override == 'grant':
                perms.add(IDENTITY_PERM)
            elif override == 'revoke':
                perms.discard(IDENTITY_PERM)
            if not has_national_scope(user_obj):
                perms = {p for p in perms if not any(fnmatch(p, pattern) for pattern in NATIONAL_ONLY)}
            user_obj._access_perm_cache = perms
        return user_obj._access_perm_cache
