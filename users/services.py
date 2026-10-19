from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode


def send_activation_email(user):
    """Email the system URL and a single-use, time-limited set-password link.

    No password is ever generated or sent; the account has an unusable password
    until the staff member sets one through the link.
    """
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    path = reverse('password_reset_confirm', kwargs={'uidb64': uid, 'token': token})
    context = {
        'user': user,
        'site_name': settings.SITE_NAME,
        'site_url': settings.SITE_URL,
        'activation_url': f'{settings.SITE_URL}{path}',
        'valid_days': settings.PASSWORD_RESET_TIMEOUT // 86400,
    }
    send_mail(
        subject=f'Your {settings.SITE_NAME} account',
        message=render_to_string('users/activation_email.txt', context),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
    )
