# tutorial/tables.py
from django_tables2.utils import A
import django_tables2 as tables
from .models import Playback, Track, Artist
from django.urls import reverse
from django.utils.http import urlencode
from django.utils.html import format_html

class PlaybackTable(tables.Table):
    announcement_1 = tables.Column(empty_values=(), verbose_name="Announcement 1")
    announcement_2 = tables.Column(empty_values=(), verbose_name="Announcement 2")
    category = tables.Column(
        accessor="trackid__categoryid__category",
        verbose_name="Category",
        order_by=("trackid__categoryid__category",),
    )
    artist = tables.Column(
        accessor="trackid__artistid__display_name",  # uses property
        verbose_name="Artist",
        order_by=("trackid__artistid__artist_display_name",
                  "trackid__artistid__artist_name"),
    )

    def render_artist(self, record):
        if record.trackid.artistid_id:
            return record.trackid.artistid.display_name
        return "—"
    
    track_name = tables.Column(
        accessor="trackid__display_name",  # uses property
        verbose_name="Track",
        order_by=("trackid__track_display_name", "trackid__track_name"),
    )
    year = tables.Column(
        accessor="trackid__yearid__year",
        verbose_name="Year",
        order_by=("trackid__yearid__year",),
        empty_values=()
    )

    def render_year(self, record):
        if record.trackid.yearid_id:
            return record.trackid.yearid.year
        return "—"

    def render_announcement_1(self, record):
        track_id = record.trackid.pk
        url = f"/media/{track_id}-1~0.mp3"
        return format_html(
            '<button class="play-btn" onclick="loadAudio(this, \'{}\')">Play</button>',
            url
            )

    def render_announcement_2(self, record):
        track_id = record.trackid.pk
        url = f"/media/{track_id}-2~0.mp3"
        return format_html(
            '<button class="play-btn" onclick="loadAudio(this, \'{}\')">Play</button>',
            url
            )
    
    edit = tables.Column(
        empty_values=(),
        verbose_name="",
    )

    def render_edit(self, record):
        # Build base URL
        url = reverse("track-artist-edit", args=[record.trackid.pk])
        
        # Add next param from current request
        if hasattr(self, "request") and self.request:
            query = urlencode({"next": self.request.get_full_path()})
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}{query}"
        
        return format_html('<a href="{}">Edit</a>', url)

    class Meta:
        model = Playback
        fields = ("category", "artist", "track_name", "year", "date", "timestamp", "announcement_1", "announcement_2", "edit")

class TracksTable(tables.Table):
    announcement_1 = tables.Column(empty_values=(), verbose_name="Announcement 1")
    announcement_2 = tables.Column(empty_values=(), verbose_name="Announcement 2")
    category = tables.Column(
        accessor="categoryid__category",
        verbose_name="Category",
        order_by=("categoryid__category",),
    )
    artist = tables.Column(
        accessor="artistid__display_name",  # uses property
        verbose_name="Artist",
        order_by=("artistid__artist_display_name",
                  "artistid__artist_name"),
    )

    def render_artist(self, record):
        if record.artistid_id:
            return record.artistid.display_name
        return "—"
    
    track_name = tables.Column(
        accessor="track_display_name",  # uses property
        verbose_name="Track",
        order_by=("track_display_name", "track_name"),
        empty_values=(),
    )

    def render_track_name(self, record):
        # If you want to keep using the property, you can even skip this
        return record.display_name or "—"

    year = tables.Column(
        accessor="yearid__year",
        verbose_name="Year",
        order_by=("yearid__year",),
        empty_values=()
    )

    def render_year(self, record):
        if record.yearid_id:
            return record.yearid.year
        return "—"
    
    edit = tables.Column(
        empty_values=(),
        verbose_name="",
    )

    def render_announcement_1(self, record):
        track_id = record.pk
        url = f"/media/{track_id}-1~0.mp3"
        return format_html(
            '<button class="play-btn" onclick="loadAudio(this, \'{}\')">Play</button>',
            url
            )

    def render_announcement_2(self, record):
        track_id = record.pk
        url = f"/media/{track_id}-2~0.mp3"
        return format_html(
            '<button class="play-btn" onclick="loadAudio(this, \'{}\')">Play</button>',
            url
            )

    def render_edit(self, record):
        # Build base URL
        url = reverse("track-artist-edit", args=[record.pk])
        
        # Add next param from current request
        if hasattr(self, "request") and self.request:
            query = urlencode({"next": self.request.get_full_path()})
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}{query}"
        
        return format_html('<a href="{}">Edit</a>', url)

    class Meta:
        model = Track
        fields = ("category", "artist", "track_name", "year", "announcement_1", "announcement_2", "edit")

class ArtistsTable(tables.Table):
    artist = tables.Column(
        accessor="artist_display_name",  # uses property
        verbose_name="Artist",
        order_by=("artist_display_name",
                  "artist_name"),
        empty_values=(),
    )

    def render_artist(self, record):
        return record.display_name or "-"
    
    edit = tables.Column(
        empty_values=(),
        verbose_name="",
    )

    def render_edit(self, record):
        # Build base URL
        url = reverse("artist-edit", args=[record.pk])
        
        # Add next param from current request
        if hasattr(self, "request") and self.request:
            query = urlencode({"next": self.request.get_full_path()})
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}{query}"
        
        return format_html('<a href="{}">Edit</a>', url)

    class Meta:
        model = Artist
        fields = ("artist", "edit")
