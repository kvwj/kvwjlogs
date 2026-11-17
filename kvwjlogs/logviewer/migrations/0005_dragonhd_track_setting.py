from django.db import migrations


def migrate_track_speech_setting(apps, schema_editor):
    settings = apps.get_model("logviewer", "UtilitySettings")
    settings.objects.using(schema_editor.connection.alias).filter(
        pk=1,
        track_speech_provider="microsoft",
        track_speech_voice__in=["nova", "shimmer", "echo", "christopher", "onyx"],
    ).update(track_speech_voice="nova_dragon_hd")


class Migration(migrations.Migration):

    dependencies = [
        ("logviewer", "0004_utilitysettings"),
    ]

    operations = [
        migrations.RunPython(migrate_track_speech_setting),
    ]
