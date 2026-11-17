# urls.py
from django.urls import path
from .views import category_audio, voice_preview, random_preview_track, track_announcement_status, utility_settings
from .views import track_and_artist_edit, artist_edit, FilteredPlaybackListView, FilteredTrackListView, FilteredArtistListView, FilteredLogListView, regenerate_artist_audio_start, regenerate_artist_audio_status, start_single_audio, audio_task_status, rds_override, rds_display_status

urlpatterns = [
    path("", FilteredPlaybackListView.as_view(), name="playback-list"),
    path("tracks", FilteredTrackListView.as_view(), name="track-list"),
    path("track/<int:pk>/edit/", track_and_artist_edit, name="track-artist-edit"),
    path("track/<int:pk>/announcement-status/", track_announcement_status, name="track-announcement-status"),
    path("artists", FilteredArtistListView.as_view(), name="artist-list"),
    path("log/", FilteredLogListView.as_view(), name="log-list"),
    path("utilities/rds-override/", rds_override, name="rds-override"),
    path("utilities/settings/", utility_settings, name="utility-settings"),
    path("utilities/category-audio/", category_audio, name="category-audio"),
    path("utilities/category-audio/preview/", voice_preview, name="voice-preview"),
    path("utilities/category-audio/preview-track/", random_preview_track, name="random-preview-track"),
    path(
        "utilities/category-audio/<uuid:task_id>/",
        category_audio,
        name="category-audio-progress",
    ),
    path("utilities/rds-display-status/", rds_display_status, name="rds-display-status"),
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
