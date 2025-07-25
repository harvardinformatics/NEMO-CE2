# Generated by Django 4.2.20 on 2025-05-09 17:44

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("NEMO", "0113_version_7_2_0"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="temporaryphysicalaccess",
            options={"ordering": ["-end_time"], "verbose_name_plural": "Temporary physical access"},
        ),
        migrations.AddField(
            model_name="tool",
            name="_staff",
            field=models.ManyToManyField(
                blank=True,
                db_table="NEMO_tool_staff",
                help_text="Users who can act as staff for this tool..",
                related_name="staff_for_tools",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
