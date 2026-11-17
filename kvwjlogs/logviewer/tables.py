# tutorial/tables.py
from django_tables2.utils import A
import django_tables2 as tables
from .models import Playback, Track, Artist, Log
from django.urls import reverse
from django.utils.http import urlencode
from django.utils.html import format_html, format_html_join
from .audio import ANNOUNCEMENT_SHIFT_CHOICES


def announcement_picker(track):
    track_id = track.pk
    def options(start, label):
        return format_html_join(
            "", '<option value="{}">{} {}</option>',
            (
                (f"/media/{track_id}-{num}~0.mp3", label, (num - 1) % 4 + 1)
                for num in range(start, start + 4)
            ),
        )
    category = track.categoryid.category if track.categoryid_id else ""
    include_eighties = "80s" in (category or "").casefold()
    groups = format_html_join(
        "", '<optgroup label="{}">{}</optgroup>',
        (
            (label, options(index * 4 + 1, label))
            for index, (voice_set, label) in enumerate(ANNOUNCEMENT_SHIFT_CHOICES)
            if voice_set != "eighties" or include_eighties
        ),
    )
    return format_html(
        '<div class="announcement-picker">'
        '<select aria-label="Announcement for track {}" class="form-select form-select-sm">'
        '{}</select>'
        '<button type="button" class="btn btn-light btn-sm" onclick="playSelectedAnnouncement(this)">Play</button>'
        '<audio controls hidden></audio><span role="status" class="text-danger"></span></div>',
        track_id, groups,
    )


class PlaybackTable(tables.Table):
    announcements = tables.Column(empty_values=(), verbose_name="Announcements", exclude_from_export=True, orderable=False)
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

    def render_announcements(self, record):
        return announcement_picker(record.trackid)

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
            "announcements",
        )

class TracksTable(tables.Table):
    announcements = tables.Column(empty_values=(), verbose_name="Announcements", exclude_from_export=True, orderable=False)
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

    def render_announcements(self, record):
        return announcement_picker(record)

    class Meta:
        model = Track
        fields = (
            "category",
            "artist",
            "track_name",
            "year",
            "announcements",
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


