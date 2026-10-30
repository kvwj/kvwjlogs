from celery import shared_task

from .models import Artist, Track
from .audio import regenerate_audio  # Adjust to your actual module


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
    track = Track.objects.select_related("artistid").get(pk=track_id)

    track_name = track.display_name
    artist_name = track.artistid.display_name if track.artistid else ""

    if num == 1:
        text = f"{artist_name} with {track_name} on J-95!"
    elif num == 2:
        text = f"{track_name}, that's {artist_name} on K-V-W-J!"
    else:
        raise ValueError(f"Unsupported announcement number: {num}")

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