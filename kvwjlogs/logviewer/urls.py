# urls.py
from django.urls import path
from .views import track_and_artist_edit, artist_edit, FilteredPlaybackListView, FilteredTrackListView, FilteredArtistListView

urlpatterns = [
    path("", FilteredPlaybackListView.as_view(), name="playback-list"),
    path("tracks", FilteredTrackListView.as_view(), name="track-list"),
    path("track/<int:pk>/edit/", track_and_artist_edit, name="track-artist-edit"),
    path("artists", FilteredArtistListView.as_view(), name="artist-list"),
    path("artist/<int:pk>/edit/", artist_edit, name="artist-edit"),
]
