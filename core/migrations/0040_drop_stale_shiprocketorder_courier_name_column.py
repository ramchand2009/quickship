from django.db import migrations


def drop_stale_courier_name_column(apps, schema_editor):
    table_name = "core_shiprocketorder"
    column_name = "courier_name"
    existing_columns = {
        column.name
        for column in schema_editor.connection.introspection.get_table_description(
            schema_editor.connection.cursor(),
            table_name,
        )
    }
    if column_name not in existing_columns:
        return

    quoted_table = schema_editor.quote_name(table_name)
    quoted_column = schema_editor.quote_name(column_name)
    schema_editor.execute(f"ALTER TABLE {quoted_table} DROP COLUMN {quoted_column}")


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0039_alter_woocommercesettings_import_statuses"),
    ]

    operations = [
        migrations.RunPython(drop_stale_courier_name_column, migrations.RunPython.noop),
    ]
