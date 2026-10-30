from django.shortcuts import render

# Create your views here.

from django.http import HttpResponse, JsonResponse


#def index(request):
#    return HttpResponse("Hello, world. You're at the log viewer index.")

from datetime import datetime, timedelta

from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.db import IntegrityError

from .models import Playback, Track, Artist
from .forms import TrackDisplayForm, ArtistDisplayForm
from .tables import PlaybackTable, TracksTable, ArtistsTable
from .audio import regenerate_audio
from .tasks import regenerate_artist_audio, regenerate_single_audio
from .filters import ArtistFilter, TrackFilter, PlaybackFilter

import django_filters
from django_filters.views import FilterView
from django_tables2.views import SingleTableMixin
from django_tables2.export.views import ExportMixin
from django_tables2.paginators import LazyPaginator
from django.views.decorators.http import require_GET, require_POST
from django.db import models
from django.db.models import Value
from django.db.models.functions import Coalesce, NullIf

from django_filters import FilterSet

from celery.result import AsyncResult
from kvwjlogs.celery import app

fourweeksago = datetime.now() - timedelta(days=29)
fourweeksago_str = fourweeksago.strftime('%Y-%m-%d')


@require_POST
def start_single_audio(request, pk, num):
    track = get_object_or_404(Track, pk=pk)

    if num not in (1, 2):
        return JsonResponse(
            {"error": "Invalid announcement number."},
            status=400,
        )

    task = regenerate_single_audio.delay(track.pk, num)

    return JsonResponse({"task_id": task.id})

@require_POST
def regenerate_artist_audio_start(request, pk):
    artist = get_object_or_404(Artist, pk=pk)

    task = regenerate_artist_audio.delay(artist.pk)

    return JsonResponse({"task_id": task.id})

@require_GET
def audio_task_status(request, task_id):
    task = AsyncResult(task_id, app=app)

    response = {
        "state": task.state,
    }

    if task.state == "PROGRESS":
        response["progress"] = task.info
    elif task.state == "SUCCESS":
        response["result"] = task.result
    elif task.state == "FAILURE":
        response["error"] = str(task.result)

    return JsonResponse(response)

@require_GET
def regenerate_artist_audio_status(request, task_id):
    task = AsyncResult(task_id)

    if task.state == "PROGRESS":
        return JsonResponse({
            "state": task.state,
            **task.info,
        })

    if task.state == "SUCCESS":
        return JsonResponse({
            "state": task.state,
            **task.result,
        })

    if task.state == "FAILURE":
        return JsonResponse({
            "state": task.state,
            "error": "The regeneration job failed.",
        })

    return JsonResponse({"state": task.state})

class FilteredPlaybackListView(ExportMixin, SingleTableMixin, FilterView):
    model = Playback
    table_class = PlaybackTable
    template_name = 'logviewer/playback.html'
    filterset_class = PlaybackFilter

    table_pagination = {
        'per_page': 250,
        'paginator_class': LazyPaginator,
    }

    def get_queryset(self):
        qs = super().get_queryset()

        return qs.select_related(
            "trackid",
            "trackid__artistid",
            "trackid__categoryid",
            "trackid__yearid",
        ).annotate(
            artist_sort_name=Coalesce(
                NullIf(
                    "trackid__artistid__artist_display_name",
                    Value("", output_field=models.TextField()),
                ),
                "trackid__artistid__artist_name",
                Value("", output_field=models.TextField()),
                output_field=models.TextField(),
            )
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
        'per_page': 250,
        'paginator_class': LazyPaginator,
    }

    def get_queryset(self):
        qs = super().get_queryset()

        return qs.select_related(
            "artistid",
            "categoryid",
            "yearid",
        ).annotate(
            artist_sort_name=Coalesce(
                NullIf(
                    "artistid__artist_display_name",
                    Value("", output_field=models.TextField()),
                ),
                "artistid__artist_name",
                Value("", output_field=models.TextField()),
                output_field=models.TextField(),
            ),
            track_sort_name=Coalesce(
                NullIf(
                    "track_display_name",
                    Value("", output_field=models.TextField()),
                ),
                "track_name",
                Value("", output_field=models.TextField()),
                output_field=models.TextField(),
            ),
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
        'per_page': 250,
        'paginator_class': LazyPaginator,
    }

    def get_queryset(self):
        qs = super().get_queryset()

        return qs.annotate(
            artist_sort_name=Coalesce(
                NullIf(
                    "artist_display_name",
                    Value("", output_field=models.TextField()),
                ),
                "artist_name",
                Value("", output_field=models.TextField()),
                output_field=models.TextField(),
            )
        )

    def get_table_kwargs(self):
        kwargs = super().get_table_kwargs()
        kwargs["request"] = self.request
        return kwargs

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

    tracks_list = artist.track_set.all()
    tracks_table = TracksTable(tracks_list)

    regenerate_all_clicked = "regenerate_all_audio" in request.POST

    artist_changed = "artist_display_name" in request.POST

    artist_saved = False

    if request.method == "POST":
        if regenerate_all_clicked:
            for track in tracks_list:
                text = artist.display_name + ' with ' + track.display_name + ' on J-95!'
                num = 1
                regenerate_audio(text, track.id, num)
                text = track.display_name + ", that's "  + artist.display_name + ' on K-V-W-J!'
                num = 2
                regenerate_audio(text, track.id, num)
            return redirect(request.path_info)
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
            "tracks_table": tracks_table
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