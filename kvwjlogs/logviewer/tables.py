# tutorial/tables.py
from django_tables2.utils import A
import django_tables2 as tables
from .models import Playback, Track, Artist, Log
from django.urls import reverse
from django.utils.http import urlencode
from django.utils.html import format_html


def announcement_button(track_id, num):
    return format_html(
        '<button type="button" class="btn btn-light btn-sm play-btn" '
        'onclick="loadAudio(this, \'{}\')">Play</button>',
        f"/media/{track_id}-{num}~0.mp3",
    )


class PlaybackTable(tables.Table):
    announcement_1 = tables.Column(empty_values=(), verbose_name="Announcement 1")
    announcement_2 = tables.Column(empty_values=(), verbose_name="Announcement 2")
    announcement_3 = tables.Column(empty_values=(), verbose_name="Announcement 3", exclude_from_export=True)
    announcement_4 = tables.Column(empty_values=(), verbose_name="Announcement 4", exclude_from_export=True)
    category = tables.Column(
        accessor="trackid__categoryid__category",
        verbose_name="Category",
        order_by=("trackid__categoryid__category",),
    )
    artist = tables.Column(
        accessor="trackid__artistid__display_name",
        verbose_name="Artist",
        order_by=("artist_sort_name",),
    )

    def render_artist(self, record):
        artist = record.trackid.artistid

        if not artist:
            return "—"

        url = reverse("artist-edit", args=[artist.pk])

        if hasattr(self, "request") and self.request:
            query = urlencode({"next": self.request.get_full_path()})
            url = f"{url}?{query}"

        return format_html(
            '<a href="{}">{}</a>',
            url,
            artist.display_name or "—",
        )

    def render_track_name(self, record):
        track = record.trackid
        url = reverse("track-artist-edit", args=[track.pk])

        if hasattr(self, "request") and self.request:
            query = urlencode({"next": self.request.get_full_path()})
            url = f"{url}?{query}"

        return format_html(
            '<a href="{}">{}</a>',
            url,
            track.display_name or "—",
        )


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
        return announcement_button(record.trackid.pk, 1)

    def render_announcement_2(self, record):
        return announcement_button(record.trackid.pk, 2)

    def render_announcement_3(self, record):
        return announcement_button(record.trackid.pk, 3)

    def render_announcement_4(self, record):
        return announcement_button(record.trackid.pk, 4)

    def value_artist(self, record):
        artist = record.trackid.artistid
        return artist.display_name if artist else "—"


    def value_track_name(self, record):
        track = record.trackid
        return track.display_name if track else "—"

    class Meta:
        model = Playback
        fields = (
            "category", "artist", "track_name", "year", "date", "timestamp",
            "announcement_1", "announcement_2", "announcement_3", "announcement_4",
        )

class TracksTable(tables.Table):
    announcement_1 = tables.Column(empty_values=(), verbose_name="Announcement 1")
    announcement_2 = tables.Column(empty_values=(), verbose_name="Announcement 2")
    announcement_3 = tables.Column(empty_values=(), verbose_name="Announcement 3", exclude_from_export=True)
    announcement_4 = tables.Column(empty_values=(), verbose_name="Announcement 4", exclude_from_export=True)
    category = tables.Column(
        accessor="categoryid__category",
        verbose_name="Category",
        order_by=("categoryid__category",),
    )
    artist = tables.Column(
        accessor="artistid__display_name",
        verbose_name="Artist",
        order_by=("artist_sort_name",),
    )

    def render_artist(self, record):
        artist = record.artistid

        if not artist:
            return "—"

        url = reverse("artist-edit", args=[artist.pk])

        if hasattr(self, "request") and self.request:
            query = urlencode({"next": self.request.get_full_path()})
            url = f"{url}?{query}"

        return format_html(
            '<a href="{}">{}</a>',
            url,
            artist.display_name or "—",
        )
    
    track_name = tables.Column(
        accessor="display_name",
        verbose_name="Track",
        order_by=("track_sort_name",),
    )

    def render_track_name(self, record):
        url = reverse("track-artist-edit", args=[record.pk])

        if hasattr(self, "request") and self.request:
            query = urlencode({"next": self.request.get_full_path()})
            url = f"{url}?{query}"

        return format_html(
            '<a href="{}">{}</a>',
            url,
            record.display_name or "-",
        )


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

    def render_announcement_1(self, record):
        return announcement_button(record.pk, 1)

    def render_announcement_2(self, record):
        return announcement_button(record.pk, 2)

    def render_announcement_3(self, record):
        return announcement_button(record.pk, 3)

    def render_announcement_4(self, record):
        return announcement_button(record.pk, 4)

    class Meta:
        model = Track
        fields = (
            "category",
            "artist",
            "track_name",
            "year",
            "announcement_1",
            "announcement_2",
            "announcement_3",
            "announcement_4",
        )
        attrs = {
            "class": "table table-sm table-hover",
        }



class ArtistsTable(tables.Table):
    artist = tables.Column(
        accessor="display_name",
        verbose_name="Artist",
        order_by=("artist_sort_name",),
    )

    def render_artist(self, record):
        url = reverse("artist-edit", args=[record.pk])

        if hasattr(self, "request") and self.request:
            query = urlencode({"next": self.request.get_full_path()})
            url = f"{url}?{query}"

        return format_html(
            '<a href="{}">{}</a>',
            url,
            record.display_name or "-",
        )

    class Meta:
        model = Artist
        fields = ("artist",)

class LogTable(tables.Table):
    artist = tables.Column(
        accessor="trackid__artistid__display_name",
        verbose_name="Artist",
    )

    track = tables.Column(
        accessor="trackid__display_name",
        verbose_name="Track",
    )

    date = tables.Column()
    timestamp = tables.Column()

    class Meta:
        model = Log
        fields = ("date", "timestamp", "artist", "track")
        attrs = {"class": "table table-sm table-hover"}

    def render_artist(self, record):
        artist = record.trackid.artistid if record.trackid else None

        if not artist:
            return "—"

        url = reverse("artist-edit", args=[artist.pk])

        if hasattr(self, "request") and self.request:
            query = urlencode({"next": self.request.get_full_path()})
            url = f"{url}?{query}"

        return format_html(
            '<a href="{}">{}</a>',
            url,
            artist.display_name or "—",
        )

    def render_track(self, record):
        track = record.trackid

        if not track:
            return "—"

        url = reverse("track-artist-edit", args=[track.pk])

        if hasattr(self, "request") and self.request:
            query = urlencode({"next": self.request.get_full_path()})
            url = f"{url}?{query}"

        return format_html(
            '<a href="{}">{}</a>',
            url,
            track.display_name or "—",
        )

    def value_artist(self, record):
        artist = record.trackid.artistid if record.trackid else None
        return artist.display_name if artist else "—"


    def value_track(self, record):
        track = record.trackid
        return track.display_name if track else "—"


