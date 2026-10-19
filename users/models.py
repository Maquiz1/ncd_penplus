from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Staff account. Job title, groups and facility scope are kept separate."""

    phone = models.CharField(max_length=30, blank=True)
    phone_verified = models.BooleanField(default=False)
    job_title = models.CharField(max_length=100, blank=True)

    class Meta:
        ordering = ['username']

    def __str__(self):
        return self.get_full_name() or self.username
