from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create default operation role groups: admin and ops_viewer."

    def handle(self, *args, **options):
        created = []
        for role_name in ["admin", "ops_viewer"]:
            _, was_created = Group.objects.get_or_create(name=role_name)
            if was_created:
                created.append(role_name)

        if created:
            self.stdout.write(self.style.SUCCESS(f"Created role groups: {', '.join(created)}"))
        else:
            self.stdout.write(self.style.WARNING("Role groups already exist: admin, ops_viewer"))
