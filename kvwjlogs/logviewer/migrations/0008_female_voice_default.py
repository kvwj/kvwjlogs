from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("logviewer", "0007_announcement_shift_times"),
    ]

    operations = [
        migrations.AlterField(
            model_name="utilitysettings",
            name="track_speech_provider",
            field=models.CharField(default="microsoft", max_length=20),
        ),
        migrations.AlterField(
            model_name="utilitysettings",
            name="track_speech_voice",
            field=models.CharField(default="nova_dragon_hd", max_length=20),
        ),
    ]
