import datetime

from django.db import migrations, models


def create_settings(apps, schema_editor):
    settings = apps.get_model("logviewer", "UtilitySettings")
    settings.objects.using(schema_editor.connection.alias).get_or_create(
        pk=1,
        defaults={"track_speech_provider": "microsoft", "track_speech_voice": "nova_dragon_hd"},
    )


class Migration(migrations.Migration):

    dependencies = [
        ("logviewer", "0006_male_track_speech_settings"),
    ]

    operations = [
        migrations.AddField(
            model_name="utilitysettings",
            name="female_shift_start",
            field=models.TimeField(default=datetime.time(5, 0)),
        ),
        migrations.AddField(
            model_name="utilitysettings",
            name="male_shift_start",
            field=models.TimeField(default=datetime.time(17, 0)),
        ),
        migrations.RunPython(create_settings, migrations.RunPython.noop),
    ]
