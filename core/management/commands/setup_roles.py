from fnmatch import fnmatch

from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand

from core.roles import ROLES


class Command(BaseCommand):
    help = 'Create or update permission groups (roles) from core/roles.py. Safe to re-run.'

    def handle(self, *args, **options):
        permissions = list(Permission.objects.select_related('content_type'))
        for role, patterns in ROLES.items():
            group, created = Group.objects.get_or_create(name=role)
            matched = [p for p in permissions
                       if any(fnmatch(f'{p.content_type.app_label}.{p.codename}', pattern) for pattern in patterns)]
            group.permissions.set(matched)
            self.stdout.write(f'{"Created" if created else "Updated"} {role}: {len(matched)} permissions')
        self.stdout.write(self.style.SUCCESS('Roles are up to date.'))
