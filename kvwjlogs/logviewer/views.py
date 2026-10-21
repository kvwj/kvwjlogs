from django.shortcuts import render

# Create your views here.

from django.http import HttpResponse


#def index(request):
#    return HttpResponse("Hello, world. You're at the log viewer index.")

from datetime import datetime, timedelta

from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.db import IntegrityError

from .models import Playback, Track, Artist
from .forms import TrackDisplayForm, ArtistDisplayForm
from .tables import PlaybackTable, TracksTable, ArtistsTable

import django_filters
from django_filters.views import FilterView
from django_tables2.views import SingleTableMixin
from django_tables2.export.views import ExportMixin
from django_tables2.paginators import LazyPaginator

from django_filters import FilterSet

fourweeksago = datetime.now() - timedelta(days=29)
fourweeksago_str = fourweeksago.strftime('%Y-%m-%d')

class PlaybackFilter(django_filters.FilterSet):
    category = django_filters.CharFilter(
        field_name="trackid__categoryid__category",
        lookup_expr="icontains",
        label="Category",  # <- clean label
    )
    artist = django_filters.CharFilter(
        field_name="trackid__artistid__artist_name",
        lookup_expr="icontains",
        label="Artist",
    )
    track_name = django_filters.CharFilter(
        field_name="trackid__track_name",
        lookup_expr="icontains",
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

    class Meta:
        model = Playback
        fields = ["category", "artist", "track_name", "year", "date"]

class TrackFilter(django_filters.FilterSet):
    category = django_filters.CharFilter(
        field_name="categoryid__category",
        lookup_expr="icontains",
        label="Category",  # <- clean label
    )
    artist = django_filters.CharFilter(
        field_name="artistid__artist_name",
        lookup_expr="icontains",
        label="Artist",
    )
    track_name = django_filters.CharFilter(
        field_name="track_name",
        lookup_expr="icontains",
        label="Track Name",
    )
    year = django_filters.CharFilter(
        field_name="yearid__year",
        lookup_expr="icontains",
        label="Year",
    )

    class Meta:
        model = Track
        fields = ["category", "artist", "track_name", "year"]

class ArtistFilter(django_filters.FilterSet):
    artist = django_filters.CharFilter(
        field_name="artist_name",
        lookup_expr="icontains",
        label="Artist",
    )

    class Meta:
        model = Artist
        fields = ["artist"]


class FilteredPlaybackListView(ExportMixin, SingleTableMixin, FilterView):
    model = Playback
    table_class = PlaybackTable
    template_name = 'logviewer/playback.html'
    filterset_class = PlaybackFilter
    table_pagination = {
    'per_page': 250,  # Adjust as needed
    'paginator_class': LazyPaginator  # For 12k+ rows
}

    def get_queryset(self):
        qs = super().get_queryset()
        return qs.select_related(
            "trackid",
            "trackid__artistid",
            "trackid__categoryid",
            "trackid__yearid",
        )
    
    def get_table_kwargs(self):
        kwargs = super().get_table_kwargs()
        kwargs["request"] = self.request
        return kwargs
    
class FilteredTrackListView(ExportMixin, SingleTableMixin, FilterView):
    model = Track
    table_class = TracksTable
    template_name = 'logviewer/tracks.html'
    filterset_class = TrackFilter
    table_pagination = {
    'per_page': 250,  # Adjust as needed
    'paginator_class': LazyPaginator  # For 12k+ rows
}

    def get_queryset(self):
        qs = super().get_queryset()
        return qs.select_related(
            "artistid",
            "categoryid",
            "yearid",
        )
    
    def get_table_kwargs(self):
        kwargs = super().get_table_kwargs()
        kwargs["request"] = self.request
        return kwargs

class FilteredArtistListView(ExportMixin, SingleTableMixin, FilterView):
    model = Artist
    table_class = ArtistsTable
    template_name = 'logviewer/artists.html'
    filterset_class = ArtistFilter
    table_pagination = {
    'per_page': 250,  # Adjust as needed
    'paginator_class': LazyPaginator  # For 12k+ rows
}

    # def get_queryset(self):
    #     qs = super().get_queryset()
    #     return qs.select_related(
    #         "artistid",
    #     )
    
    def get_table_kwargs(self):
        kwargs = super().get_table_kwargs()
        kwargs["request"] = self.request
        return kwargs
    
def regenerate_audio(text, trackid, num):
    from pathlib import Path
    from openai import OpenAI
    client = OpenAI()
    speech_file_path = Path(str('/mnt/announcements/media')) / Path(str(trackid) + '-' + str(num)+ "~0.mp3")

    with client.audio.speech.with_streaming_response.create(
        model="gpt-4o-mini-tts",
        voice="nova",
        input=text,
        instructions="Speak with excitement like a radio DJ",
    ) as response:
        response.stream_to_file(speech_file_path)



def track_and_artist_edit(request, pk):
    track = get_object_or_404(Track, pk=pk)
    artist = track.artistid

    track_changed = "track_display_name" in request.POST
    artist_changed = "artist_display_name" in request.POST
    regenerate1_clicked = "regenerate_audio_1" in request.POST
    regenerate2_clicked = "regenerate_audio_2" in request.POST

    track_saved = False
    artist_saved = False

    if request.method == "POST":
        if regenerate1_clicked:
            text = artist.display_name + ' with ' + track.display_name + ' on J-95!'
            num = 1
            regenerate_audio(text, track.id, num)
            return redirect(request.path_info)
        if regenerate2_clicked:
            text = track.display_name + ", that's "  + artist.display_name + ' on K-V-W-J!'
            num = 2
            regenerate_audio(text, track.id, num)
            return redirect(request.path_info)
        if track_changed:
            track_form = TrackDisplayForm(request.POST, instance=track)
            if track_form.is_valid():
                try:
                    track_form.save()
                    track_saved = True
                except IntegrityError:
                    track_form.add_error(None, "Display name already exists.")
        else:
            track_form = TrackDisplayForm(instance=track)

        if artist_changed:
            artist_form = ArtistDisplayForm(request.POST, instance=artist)
            if artist_form.is_valid():
                try:
                    artist_form.save()
                    artist_saved = True
                except IntegrityError:
                    artist_form.add_error(None, "Display name already exists.")
        else:
            artist_form = ArtistDisplayForm(instance=artist)

        if track_saved or artist_saved:
            next_url = request.POST.get("next") or reverse("playback-list")
            return redirect(next_url)
    else:
        track_form = TrackDisplayForm(instance=track)
        artist_form = ArtistDisplayForm(instance=artist)

    return render(
        request,
        "logviewer/track_artist_edit.html",
        {
            "track": track,
            "artist": artist,
            "track_form": track_form,
            "artist_form": artist_form,
        },
    )

def artist_edit(request, pk):
    artist = get_object_or_404(Artist, pk=pk)

    artist_changed = "artist_display_name" in request.POST

    artist_saved = False

    if request.method == "POST":
        if artist_changed:
            artist_form = ArtistDisplayForm(request.POST, instance=artist)
            if artist_form.is_valid():
                try:
                    artist_form.save()
                    artist_saved = True
                except IntegrityError:
                    artist_form.add_error(None, "Display name already exists.")
        else:
            artist_form = ArtistDisplayForm(instance=artist)

        if artist_saved:
            next_url = request.POST.get("next") or reverse("playback-list")
            return redirect(next_url)
    else:
        artist_form = ArtistDisplayForm(instance=artist)

    return render(
        request,
        "logviewer/artist_edit.html",
        {
            "artist": artist,
            "artist_form": artist_form,
        },
    )



def index(request):
    latest_month_list = Playback.objects.filter(
        date__startswith='2026-03'
    ).select_related("trackid", "trackid__artistid")

    output = ", ".join(
        [
            p.trackid.artistid.artist_display_name
            or p.trackid.artistid.artist_name
            or ""
            for p in latest_month_list
        ]
    )
    return HttpResponse(output)

def tracks(request):
    tracks_list = Track.objects.all(
    ).select_related("artistid")

    output = ", ".join(
        [
            p.artistid.artist_display_name
            or p.artistid.artist_name
            or ""
            for p in tracks_list
        ]
    )
    return HttpResponse(output)

def artists(request):
    artists_list = Artist.objects.all(
    )

    output = ", ".join(
        [
            p.artistid.artist_display_name
            or p.artistid.artist_name
            or ""
            for p in artists_list
        ]
    )
    return HttpResponse(output)