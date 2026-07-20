from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create legacy operation groups. Tenant roles are stored as memberships."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report missing legacy groups without changing the database.",
        )

    def handle(self, *args, **options):
        role_names = ["admin", "ops_viewer"]
        if options["dry_run"]:
            existing = set(Group.objects.filter(name__in=role_names).values_list("name", flat=True))
            missing = [role_name for role_name in role_names if role_name not in existing]
            summary = ", ".join(missing) if missing else "none"
            self.stdout.write(f"Dry run: missing legacy groups: {summary}")
            self.stdout.write("warehouse_operator remains tenant-membership-only; no global group is created.")
            return

        created = []
        for role_name in role_names:
            _, was_created = Group.objects.get_or_create(name=role_name)
            if was_created:
                created.append(role_name)

        if created:
            self.stdout.write(self.style.SUCCESS(f"Created role groups: {', '.join(created)}"))
        else:
            self.stdout.write(self.style.WARNING("Role groups already exist: admin, ops_viewer"))
