import datetime

from django.db import migrations, models


def update_shift_times(apps, schema_editor):
    settings = apps.get_model("logviewer", "UtilitySettings")
    settings.objects.using(schema_editor.connection.alias).filter(pk=1).update(
        female_shift_start=datetime.time(6),
        male_shift_start=datetime.time(12),
        second_male_shift_start=datetime.time(19),
    )


class Migration(migrations.Migration):
    dependencies = [
        ("logviewer", "0009_update_female_nova_setting"),
    ]

    operations = [
        migrations.AddField(
            model_name="utilitysettings",
            name="second_male_track_speech_provider",
            field=models.CharField(default="microsoft", max_length=20),
        ),
        migrations.AddField(
            model_name="utilitysettings",
            name="second_male_track_speech_voice",
            field=models.CharField(default="davis_dragon_hd", max_length=20),
        ),
        migrations.AddField(
            model_name="utilitysettings",
            name="second_male_shift_start",
            field=models.TimeField(default=datetime.time(19)),
        ),
        migrations.AlterField(
            model_name="utilitysettings",
            name="female_shift_start",
            field=models.TimeField(default=datetime.time(6)),
        ),
        migrations.AlterField(
            model_name="utilitysettings",
            name="male_shift_start",
            field=models.TimeField(default=datetime.time(12)),
        ),
        migrations.RunPython(update_shift_times, migrations.RunPython.noop),
    ]
