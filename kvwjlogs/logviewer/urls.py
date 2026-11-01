# urls.py
from django.urls import path
from .views import track_and_artist_edit, artist_edit, FilteredPlaybackListView, FilteredTrackListView, FilteredArtistListView, FilteredLogListView, regenerate_artist_audio_start, regenerate_artist_audio_status, start_single_audio, audio_task_status

urlpatterns = [
    path("", FilteredPlaybackListView.as_view(), name="playback-list"),
    path("tracks", FilteredTrackListView.as_view(), name="track-list"),
    path("track/<int:pk>/edit/", track_and_artist_edit, name="track-artist-edit"),
    path("artists", FilteredArtistListView.as_view(), name="artist-list"),
    path("log/", FilteredLogListView.as_view(), name="log-list"),
    path("artist/<int:pk>/edit/", artist_edit, name="artist-edit"),
    path(
        "artist/<int:pk>/regenerate-audio/",
        regenerate_artist_audio_start,
        name="regenerate-artist-audio-start",
    ),
    path(
        "audio-regeneration/<str:task_id>/",
        regenerate_artist_audio_status,
        name="regenerate-artist-audio-status",
    ),
    path(
    "track/<int:pk>/generate-audio/<int:num>/",
    start_single_audio,
    name="start-single-audio",
    ),
    path(
    "audio-task/<str:task_id>/status/",
    audio_task_status,
    name="audio-task-status",
),
]
