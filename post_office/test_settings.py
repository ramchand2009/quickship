"""Isolated settings for post_office tests while core migration branches coexist."""

from Ram_codex1.settings import *  # noqa: F401,F403


DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
MIGRATION_MODULES = {"core": None}
