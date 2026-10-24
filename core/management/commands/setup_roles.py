from fnmatch import fnmatch

from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand

from core.roles import EDCS_POSITION_NAMES, POSITIONS, ROLES


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
        self.setup_positions()
        self.stdout.write(self.style.SUCCESS('Roles and positions are up to date.'))

    def setup_positions(self):
        from users.models import Position, User

        for order, (code, name, role, level, note) in enumerate(POSITIONS, start=1):
            Position.objects.update_or_create(code=code, defaults={
                'name': name, 'default_role': Group.objects.get(name=role), 'typical_level': level,
                'description': note, 'order': order})
        # Staff migrated from the EDCS carry the old position name as their job title.
        linked = 0
        for title, code in EDCS_POSITION_NAMES.items():
            linked += User.objects.filter(position__isnull=True, job_title=title).update(
                position=Position.objects.get(code=code), job_title='')
        self.stdout.write(f'{len(POSITIONS)} positions; {linked} migrated staff linked to their position')
