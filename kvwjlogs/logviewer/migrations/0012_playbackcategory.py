from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("logviewer", "0011_eighties_show_times"),
    ]

    operations = [
        migrations.CreateModel(
            name="PlaybackCategory",
            fields=[
                ("playback", models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE, primary_key=True,
                    related_name="category_snapshot", serialize=False, to="logviewer.playback",
                )),
                ("category", models.TextField()),
                ("inferred", models.BooleanField(default=False)),
            ],
            options={"db_table": "playback_category"},
        ),
    ]
