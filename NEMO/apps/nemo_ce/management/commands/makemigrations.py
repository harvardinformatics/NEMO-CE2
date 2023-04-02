from logging import getLogger

from django.apps import apps
from django.core.management.commands.makemigrations import Command as MigrationCommand
from django.db.migrations import Migration, writer
from django.db.migrations.autodetector import MigrationAutodetector
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.questioner import NonInteractiveMigrationQuestioner
from django.db.migrations.state import ProjectState

ORIGINAL_APP_NAME = "NEMO"
MOVE_TO_APP_NAME = "nemo_ce"
MIGRATION_TEMPLATE_LINE_ORIGINAL = "class Migration(migrations.Migration):"
NEW_MIGRATION_TEMPLATE_LINE = """\
from NEMO.apps.nemo_ce.migration_utils import NEMOMigration

class Migration(NEMOMigration):"""


# Overriding makemigrations with some custom code to write migrations in the correct app
class Command(MigrationCommand):
    help = f"Creates new migration(s) for apps, using {MOVE_TO_APP_NAME} in place of {ORIGINAL_APP_NAME}"

    def write_migration_files(self, changes):
        if ORIGINAL_APP_NAME in changes:
            # There should only be one migration for the original app
            if len(changes.get(ORIGINAL_APP_NAME)) == 1:
                # Save original migration template
                original_template = writer.MIGRATION_TEMPLATE
                # Take original changes out of the dict and set name, app and dependencies to the new one
                new_migration: Migration = next(iter(changes.pop(ORIGINAL_APP_NAME)))
                try:
                    ce_migration: Migration = next_migration_for_app(MOVE_TO_APP_NAME)
                    new_migration.app_label = ce_migration.app_label
                    new_migration.name = ce_migration.name
                    new_migration.dependencies += ce_migration.dependencies
                    # Temporarily change the migration template
                    writer.MIGRATION_TEMPLATE = writer.MIGRATION_TEMPLATE.replace(
                        MIGRATION_TEMPLATE_LINE_ORIGINAL, NEW_MIGRATION_TEMPLATE_LINE
                    )
                    # Write migration for the new app
                    super().write_migration_files({MOVE_TO_APP_NAME: [new_migration]})
                except Exception as e:
                    getLogger(__name__).exception(e)
                finally:
                    # Set the template back
                    writer.MIGRATION_TEMPLATE = original_template
        # Write all the other migrations
        super().write_migration_files(changes)


def next_migration_for_app(app_label) -> Migration:
    # Create an empty migration for app_label and return it
    loader = MigrationLoader(None, ignore_no_migrations=True)
    detector = MigrationAutodetector(
        loader.project_state(),
        ProjectState.from_apps(apps),
        NonInteractiveMigrationQuestioner(specified_apps=app_label),
    )
    changes = detector.arrange_for_graph({app_label: [Migration("custom", app_label)]}, loader.graph)
    return next(iter(changes[app_label]))
