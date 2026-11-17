from django.core.management.base import BaseCommand, CommandError

from logviewer.audio import announcement_path
from logviewer.models import AnnouncementVoice, Track


class Command(BaseCommand):
    help = "Record OpenAI/Nova for existing announcement files without voice records."

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirm-openai-nova", action="store_true",
            help="Confirm that existing untracked announcement files use OpenAI/Nova.",
        )

    def handle(self, *args, **options):
        if not options["confirm_openai_nova"]:
            raise CommandError("Pass --confirm-openai-nova after verifying the existing files.")
        if not announcement_path(0, 1).parent.is_dir():
            raise CommandError("The announcement directory is unavailable; mount it first.")

        created = existing = 0
        for track_id in Track.objects.values_list("pk", flat=True).iterator():
            for variant in (1, 2, 3, 4):
                if announcement_path(track_id, variant).is_file():
                    _, was_created = AnnouncementVoice.objects.get_or_create(
                        track_id=track_id,
                        variant=variant,
                        defaults={"provider": "openai", "voice": "nova"},
                    )
                    if was_created:
                        created += 1
                    else:
                        existing += 1
        self.stdout.write(
            f"Recorded {created} existing OpenAI/Nova files; "
            f"left {existing} existing records unchanged."
        )
