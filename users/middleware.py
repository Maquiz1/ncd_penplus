from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse


class MustChangePasswordMiddleware:
    """After an administrator set the password, the person can only open the change-password page until they
    choose their own."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        if user is not None and user.is_authenticated and user.must_change_password:
            allowed = (reverse('password_change'), reverse('password_change_done'), reverse('logout'))
            if request.path not in allowed and not request.path.startswith('/static/'):
                messages.info(request, 'Choose your own password to continue.')
                return redirect('password_change')
        return self.get_response(request)
