import django_filters
from django.db.models import Q

from .models import Artist, Track, Playback


class PlaybackFilter(django_filters.FilterSet):
    category = django_filters.CharFilter(
        field_name="trackid__categoryid__category",
        lookup_expr="icontains",
        label="Category",
    )
    artist = django_filters.CharFilter(
        method="filter_artist",
        label="Artist",
    )
    track_name = django_filters.CharFilter(
        method="filter_track_name",
        label="Track Name",
    )
    year = django_filters.CharFilter(
        field_name="trackid__yearid__year",
        lookup_expr="icontains",
        label="Year",
    )
    date = django_filters.CharFilter(
        field_name="date",
        lookup_expr="contains",
        label="Date",
    )

    def filter_artist(self, queryset, name, value):
        return queryset.filter(
            Q(trackid__artistid__artist_name__icontains=value)
            | Q(trackid__artistid__artist_display_name__icontains=value)
        )

    def filter_track_name(self, queryset, name, value):
        return queryset.filter(
            Q(trackid__track_name__icontains=value)
            | Q(trackid__track_display_name__icontains=value)
        )

    class Meta:
        model = Playback
        fields = ["category", "artist", "track_name", "year", "date"]


class TrackFilter(django_filters.FilterSet):
    category = django_filters.CharFilter(
        field_name="categoryid__category",
        lookup_expr="icontains",
        label="Category",
    )
    artist = django_filters.CharFilter(
        method="filter_artist",
        label="Artist",
    )
    track_name = django_filters.CharFilter(
        method="filter_track_name",
        label="Track Name",
    )
    year = django_filters.CharFilter(
        field_name="yearid__year",
        lookup_expr="icontains",
        label="Year",
    )

    def filter_artist(self, queryset, name, value):
        return queryset.filter(
            Q(artistid__artist_name__icontains=value)
            | Q(artistid__artist_display_name__icontains=value)
        )

    def filter_track_name(self, queryset, name, value):
        return queryset.filter(
            Q(track_name__icontains=value)
            | Q(track_display_name__icontains=value)
        )

    class Meta:
        model = Track
        fields = ["category", "artist", "track_name", "year"]


class ArtistFilter(django_filters.FilterSet):
    artist = django_filters.CharFilter(
        method="filter_artist",
        label="Artist",
    )

    def filter_artist(self, queryset, name, value):
        return queryset.filter(
            Q(artist_name__icontains=value)
            | Q(artist_display_name__icontains=value)
        )

    class Meta:
        model = Artist
        fields = ["artist"]
