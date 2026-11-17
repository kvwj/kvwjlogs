from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("logviewer", "0005_dragonhd_track_setting"),
    ]

    operations = [
        migrations.AddField(
            model_name="utilitysettings",
            name="male_track_speech_provider",
            field=models.CharField(default="microsoft", max_length=20),
        ),
        migrations.AddField(
            model_name="utilitysettings",
            name="male_track_speech_voice",
            field=models.CharField(default="andrew_dragon_hd", max_length=20),
        ),
    ]
