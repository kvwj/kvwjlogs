from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("logviewer", "0002_artist_category_log_track_year_rdsdisplaystate"),
    ]

    operations = [
        migrations.CreateModel(
            name="AnnouncementVoice",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("track_id", models.IntegerField()),
                ("variant", models.PositiveSmallIntegerField()),
                ("provider", models.CharField(blank=True, max_length=20)),
                ("voice", models.CharField(blank=True, max_length=20)),
            ],
            options={
                "db_table": "announcement_voice",
                "constraints": [
                    models.UniqueConstraint(fields=("track_id", "variant"), name="announcement_voice_track_variant"),
                ],
            },
        ),
    ]
