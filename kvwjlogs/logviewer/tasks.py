from celery import shared_task
import logging

from .models import Artist, Track, Category
from .audio import regenerate_audio, announcement_path


logger = logging.getLogger(__name__)


@shared_task(bind=True)
def generate_category_audio(self, category_id, variants, mode):
    if mode not in ("missing", "regenerate"):
        raise ValueError("Unsupported generation mode.")
    if not variants or any(num not in (1, 2, 3, 4) for num in variants):
        raise ValueError("Select valid announcement variants.")

    category = Category.objects.get(pk=category_id)
    tracks = Track.objects.filter(categoryid=category).select_related(
        "artistid", "yearid"
    ).order_by("pk")
    progress = {
        "total": tracks.count() * len(variants),
        "processed": 0,
        "generated": 0,
        "skipped": 0,
        "failed": 0,
        "errors": [],
        "current_track": "",
    }
    self.update_state(state="PROGRESS", meta=progress.copy())

    for track in tracks.iterator():
        title = track.display_name
        artist = track.artistid.display_name if track.artistid else ""
        year = track.yearid.year if track.yearid else None
        texts = {
            1: f"{artist} with {title} on J-95!",
            2: f"{title}, that's {artist} on K-V-W-J!",
            3: f"{year} music from {artist} on K-V-W-J!",
            4: f"That was {title} from {year} on J-95!",
        }
        progress["current_track"] = " - ".join(
            part for part in (title, artist, str(year) if year is not None else "") if part
        )
        for num in variants:
            try:
                if (
                    (num in (3, 4) and year is None)
                    or (
                        mode == "missing"
                        and announcement_path(track.pk, num).is_file()
                    )
                ):
                    progress["skipped"] += 1
                else:
                    regenerate_audio(texts[num], track.pk, num)
                    progress["generated"] += 1
            except Exception as exc:
                logger.exception(
                    "Category audio generation failed for track %s, variant %s",
                    track.pk, num,
                )
                progress["failed"] += 1
                progress["errors"].append(
                    f"Track {track.pk} ({title}), announcement {num}: {exc}"
                )
            progress["processed"] += 1
            self.update_state(state="PROGRESS", meta=progress.copy())

    return progress


@shared_task(bind=True)
def regenerate_artist_audio(self, artist_id):
    artist = Artist.objects.get(pk=artist_id)

    tracks = Track.objects.filter(artistid=artist)

    total = len(tracks) * 2
    completed = 0
    track_statuses = {}

    for track in tracks:
        track_id = track.pk
        track_name = track.display_name

        track_statuses[str(track_id)] = {
            "name": track_name,
            "status": "in_progress",
            "completed": 0,
        }

        announcements = [
            (
                f"{artist.display_name} with {track_name} on J-95!",
                1,
            ),
            (
                f"{track_name}, that's "
                f"{artist.display_name} on K-V-W-J!",
                2,
            ),
        ]

        for text, num in announcements:
            self.update_state(
                state="PROGRESS",
                meta={
                    "total": total,
                    "completed": completed,
                    "tracks": track_statuses,
                    "current_track": track_name,
                    "current_announcement": num,
                },
            )

            try:
                regenerate_audio(text, track_id, num)
                completed += 1

                track_statuses[str(track_id)]["completed"] += 1

                if track_statuses[str(track_id)]["completed"] == 2:
                    track_statuses[str(track_id)]["status"] = "complete"

            except Exception as exc:
                track_statuses[str(track_id)]["status"] = "failed"
                track_statuses[str(track_id)]["error"] = str(exc)

                # Continue processing other announcements and tracks.

            track_label = track_name

            self.update_state(
                state="PROGRESS",
                meta={
                    "total": total,
                    "completed": completed,
                    "tracks": track_statuses,
                    "current_track": track_label,
                    "current_announcement": num,
                },
            )

    return {
        "total": total,
        "completed": completed,
        "tracks": track_statuses,
    }

@shared_task(bind=True)
def regenerate_single_audio(self, track_id, num):
    if num not in (1, 2, 3, 4):
        raise ValueError(f"Unsupported announcement number: {num}")

    track = Track.objects.select_related("artistid", "yearid").get(pk=track_id)

    track_name = track.display_name
    artist_name = track.artistid.display_name if track.artistid else ""
    year = track.yearid.year if track.yearid else None

    texts = {
        1: f"{artist_name} with {track_name} on J-95!",
        2: f"{track_name}, that's {artist_name} on K-V-W-J!",
        3: f"{year} music from {artist_name} on K-V-W-J!",
        4: f"That was {track_name} from {year} on J-95!",
    }
    if num in (3, 4) and year is None:
        raise ValueError(
            f"Announcement {num} requires a year for track {track_id}."
        )
    text = texts[num]

    self.update_state(
        state="PROGRESS",
        meta={
            "track_id": track_id,
            "track_name": track_name,
            "announcement": num,
            "status": "in_progress",
        },
    )

    regenerate_audio(text, track_id, num)

    return {
        "track_id": track_id,
        "track_name": track_name,
        "announcement": num,
        "status": "complete",
    }