from django.db import migrations


def update_female_nova_setting(apps, schema_editor):
    settings = apps.get_model("logviewer", "UtilitySettings")
    settings.objects.using(schema_editor.connection.alias).filter(
        pk=1, track_speech_provider="openai", track_speech_voice="nova",
    ).update(track_speech_provider="microsoft", track_speech_voice="nova_dragon_hd")


class Migration(migrations.Migration):

    dependencies = [
        ("logviewer", "0008_female_voice_default"),
    ]

    operations = [
        migrations.RunPython(update_female_nova_setting),
    ]
