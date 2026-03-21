from django.core.management.base import BaseCommand

from core.models import Project


SAMPLE_PROJECTS = [
    {
        "name": "Community Insights",
        "description": "Captures urban analytics data for civic dashboard experiments.",
    },
    {
        "name": "Learning Platform",
        "description": "Structured learning modules, progress tracking, and certification automation.",
    },
    {
        "name": "Marketplace API",
        "description": "Headless commerce API with product, pricing, and inventory orchestration.",
    },
]


class Command(BaseCommand):
    help = "Seed the database with sample Project entries if they are missing."

    def handle(self, *args, **kwargs):
        created = 0
        for data in SAMPLE_PROJECTS:
            project, was_created = Project.objects.get_or_create(
                name=data["name"],
                defaults={"description": data["description"]},
            )
            if was_created:
                created += 1

        if created:
            self.stdout.write(self.style.SUCCESS(f"Created {created} sample project(s)."))
        else:
            self.stdout.write(self.style.WARNING("Sample projects already exist."))
