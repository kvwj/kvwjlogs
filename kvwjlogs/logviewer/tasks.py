from celery import shared_task
import base64
import logging
from pathlib import Path
from tempfile import TemporaryDirectory

from django.db.models import Q

from .models import Artist, Track, Category
from .audio import (
    EIGHTIES_SHOW_PROVIDER, EIGHTIES_SHOW_VOICE, regenerate_audio, announcement_path,
    speech_title, synthesize_audio, validate_speech_options,
)


logger = logging.getLogger(__name__)


def announcement_texts(artist, title, year):
    return {
        1: f"{artist} with {title} on J-95!",
        2: f"{title}, that's {artist} on K-V-W-J!",
        3: f"{year} music from {artist} on K-V-W-J!",
        4: f"That was {title} from {year} on J-95!",
    }


def validate_announcement_voice(variants, provider, voice):
    if any(num >= 13 for num in variants) and (provider, voice) != (
        EIGHTIES_SHOW_PROVIDER, EIGHTIES_SHOW_VOICE,
    ):
        raise ValueError("80s show announcements require Microsoft Emma DragonHD.")


def preview_tracks():
    title_present = (
        (Q(track_display_name__isnull=False) & ~Q(track_display_name=""))
        | (Q(track_name__isnull=False) & ~Q(track_name=""))
    )
    artist_present = (
        (Q(artistid__artist_display_name__isnull=False) & ~Q(artistid__artist_display_name=""))
        | (Q(artistid__artist_name__isnull=False) & ~Q(artistid__artist_name=""))
    )
    return Track.objects.select_related("artistid").filter(
        title_present, artist_present,
    )


@shared_task
def generate_voice_preview(provider, voice, track_id=None):
    validate_speech_options(provider, voice, allow_legacy=True)
    if track_id is None:
        track = preview_tracks().order_by("?").first()
    else:
        track = preview_tracks().filter(pk=track_id).first()
    if track is None:
        if track_id is None:
            raise ValueError("No tracks with a title and artist are available for voice previews.")
        raise ValueError("The selected preview track is no longer available. Choose another track.")
    artist = track.artistid.display_name
    title = speech_title(track.display_name, provider, voice)
    text = f"{artist} with {title} on J-95!"
    with TemporaryDirectory() as folder:
        path = Path(folder) / "preview.mp3"
        synthesize_audio(text, path, provider, voice)
        return {
            "audio": base64.b64encode(path.read_bytes()).decode("ascii"),
            "track_title": track.display_name,
            "track_artist": artist,
        }


@shared_task(bind=True)
def generate_category_audio(
    self, category_id, variants, mode, provider="openai", voice="nova",
):
    validate_speech_options(provider, voice, allow_legacy=True)
    if mode not in ("missing", "regenerate"):
        raise ValueError("Unsupported generation mode.")
    if not variants or any(num not in range(1, 17) for num in variants):
        raise ValueError("Select valid announcement variants.")
    validate_announcement_voice(variants, provider, voice)

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
        spoken_title = speech_title(title, provider, voice)
        artist = track.artistid.display_name if track.artistid else ""
        year = track.yearid.year if track.yearid else None
        texts = announcement_texts(artist, spoken_title, year)
        progress["current_track"] = " - ".join(
            part for part in (title, artist, str(year) if year is not None else "") if part
        )
        for num in variants:
            variety = (num - 1) % 4 + 1
            try:
                if (
                    (variety in (3, 4) and year is None)
                    or (
                        mode == "missing"
                        and announcement_path(track.pk, num).is_file()
                    )
                ):
                    progress["skipped"] += 1
                else:
                    regenerate_audio(
                        texts[variety], track.pk, num, provider=provider, voice=voice,
                    )
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
def regenerate_artist_audio(
    self, artist_id, variants=None, provider="openai", voice="nova", mode="regenerate",
):
    validate_speech_options(provider, voice, allow_legacy=True)
    if mode not in ("missing", "regenerate"):
        raise ValueError("Unsupported generation mode.")
    if variants is None:
        variants = [1, 2]
    if not variants or any(num not in range(1, 17) for num in variants):
        raise ValueError("Select valid announcement variants.")
    validate_announcement_voice(variants, provider, voice)
    artist = Artist.objects.get(pk=artist_id)

    tracks = Track.objects.filter(artistid=artist).select_related("yearid")

    total = len(tracks) * len(variants)
    completed = 0
    skipped = 0
    track_statuses = {}

    for track in tracks:
        track_id = track.pk
        track_name = track.display_name
        year = track.yearid.year if track.yearid else None
        texts = announcement_texts(artist.display_name, speech_title(track_name, provider, voice), year)

        track_statuses[str(track_id)] = {
            "name": track_name,
            "status": "in_progress",
            "completed": 0,
            "skipped": 0,
            "total": len(variants),
        }
        for num in variants:
            variety = (num - 1) % 4 + 1
            self.update_state(
                state="PROGRESS",
                meta={
                    "total": total,
                    "completed": completed,
                    "skipped": skipped,
                    "tracks": track_statuses,
                    "current_track": track_name,
                    "current_announcement": num,
                },
            )

            try:
                if (
                    (variety in (3, 4) and year is None)
                    or (mode == "missing" and announcement_path(track_id, num).is_file())
                ):
                    skipped += 1
                    track_statuses[str(track_id)]["skipped"] += 1
                else:
                    regenerate_audio(texts[variety], track_id, num, provider=provider, voice=voice)
                    completed += 1
                    track_statuses[str(track_id)]["completed"] += 1

            except Exception as exc:
                logger.exception("Artist audio generation failed for track %s, variant %s", track_id, num)
                track_statuses[str(track_id)]["status"] = "failed"
                track_statuses[str(track_id)]["error"] = str(exc)

                # Continue processing other announcements and tracks.

            track_label = track_name

            self.update_state(
                state="PROGRESS",
                meta={
                    "total": total,
                    "completed": completed,
                    "skipped": skipped,
                    "tracks": track_statuses,
                    "current_track": track_label,
                    "current_announcement": num,
                },
            )
        if track_statuses[str(track_id)]["status"] != "failed":
            track_statuses[str(track_id)]["status"] = "complete"

    return {
        "total": total,
        "completed": completed,
        "skipped": skipped,
        "tracks": track_statuses,
    }

@shared_task(bind=True)
def regenerate_single_audio(self, track_id, num, provider="openai", voice="nova"):
    validate_speech_options(provider, voice, allow_legacy=True)
    if num not in range(1, 17):
        raise ValueError(f"Unsupported announcement number: {num}")
    validate_announcement_voice([num], provider, voice)

    track = Track.objects.select_related("artistid", "yearid").get(pk=track_id)

    track_name = track.display_name
    spoken_title = speech_title(track_name, provider, voice)
    artist_name = track.artistid.display_name if track.artistid else ""
    year = track.yearid.year if track.yearid else None

    texts = announcement_texts(artist_name, spoken_title, year)
    variety = (num - 1) % 4 + 1
    if variety in (3, 4) and year is None:
        raise ValueError(
            f"Announcement {num} requires a year for track {track_id}."
        )
    text = texts[variety]

    self.update_state(
        state="PROGRESS",
        meta={
            "track_id": track_id,
            "track_name": track_name,
            "announcement": num,
            "status": "in_progress",
        },
    )

    regenerate_audio(text, track_id, num, provider=provider, voice=voice)

    return {
        "track_id": track_id,
        "track_name": track_name,
        "announcement": num,
        "status": "complete",
    }