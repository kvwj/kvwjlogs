import datetime

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("logviewer", "0010_third_announcement_shift"),
    ]

    operations = [
        migrations.AddField(
            model_name="utilitysettings",
            name="eighties_show_start",
            field=models.TimeField(default=datetime.time(21)),
        ),
        migrations.AddField(
            model_name="utilitysettings",
            name="eighties_show_end",
            field=models.TimeField(default=datetime.time(0)),
        ),
    ]
