from django.shortcuts import render

# Create your views here.

from django.http import HttpResponse, JsonResponse


#def index(request):
#    return HttpResponse("Hello, world. You're at the log viewer index.")

from datetime import datetime, timedelta
import logging
import stat
from kombu.exceptions import KombuError

from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.db import IntegrityError

from .models import (
    Playback, Track, Artist, Log, RdsDisplayState, AnnouncementVoice, UtilitySettings,
)
from .forms import (
    TrackDisplayForm, ArtistDisplayForm, RdsOverrideForm, CategoryAudioForm,
    SpeechSelectionForm, ArtistAudioForm, AnnouncementShiftForm,
)
from .tables import PlaybackTable, TracksTable, ArtistsTable, LogTable
from .audio import (
    EIGHTIES_SHOW_PROVIDER, EIGHTIES_SHOW_VOICE, announcement_path, regenerate_audio,
    speech_title, SPEECH_PROVIDERS, SPEECH_VOICES,
    SPEECH_VOICE_GENDERS, LEGACY_MICROSOFT_VOICES, validate_speech_options,
)
from .tasks import (
    regenerate_artist_audio, regenerate_single_audio, generate_category_audio,
    generate_voice_preview, preview_tracks,
)
from .filters import ArtistFilter, TrackFilter, PlaybackFilter

import django_filters
from django_filters.views import FilterView
from django_tables2.views import SingleTableMixin
from django.views.generic import TemplateView
from django_tables2.export.views import ExportMixin
from django_tables2.paginators import LazyPaginator
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from django.views.decorators.cache import never_cache
from django.db import models
from django.db.models import Value
from django.db.models.functions import Coalesce, NullIf

from django_filters import FilterSet

from celery.result import AsyncResult
from kvwjlogs.celery import app

fourweeksago = datetime.now() - timedelta(days=29)
fourweeksago_str = fourweeksago.strftime('%Y-%m-%d')


@require_http_methods(["GET", "POST"])
def category_audio(request, task_id=None):
    form = CategoryAudioForm(request.POST if request.method == "POST" else None)
    status = 200
    if request.method == "POST" and form.is_valid():
        try:
            task = generate_category_audio.delay(
                form.cleaned_data["category"].pk,
                form.cleaned_data["variants"],
                form.cleaned_data["mode"],
                provider=form.cleaned_data["provider"],
                voice=form.cleaned_data["voice"],
            )
        except KombuError:
            logging.getLogger(__name__).exception("Could not queue category audio generation")
            form.add_error(None, "Could not start generation. Check the Celery broker and try again.")
            status = 503
        else:
            return redirect("category-audio-progress", task_id=task.id)
    return render(
        request,
        "logviewer/category_audio.html",
        {"form": form, "task_id": task_id, "speech_voices": SPEECH_VOICES},
        status=status,
    )


def preview_track_id(value):
    if not value or not value.isascii() or not value.isdecimal() or len(value) > 19:
        raise ValueError("Invalid preview track ID.")
    track_id = int(value)
    if not 1 <= track_id <= 9223372036854775807:
        raise ValueError("Invalid preview track ID.")
    return track_id


@require_GET
@never_cache
def random_preview_track(request):
    exclude = request.GET.get("exclude")
    tracks = preview_tracks()
    if exclude is not None:
        try:
            track_id = preview_track_id(exclude)
        except ValueError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
        tracks = tracks.exclude(pk=track_id)
    track = tracks.order_by("?").first()
    if track is None:
        message = (
            "No other tracks with a title and artist are available."
            if exclude is not None
            else "No tracks with a title and artist are available for voice previews."
        )
        return JsonResponse({"error": message}, status=404)
    return JsonResponse({
        "track_id": str(track.pk), "track_title": track.display_name,
        "track_artist": track.artistid.display_name,
    })


@require_POST
def voice_preview(request):
    provider = request.POST.get("provider", "")
    voice = request.POST.get("voice", "")
    try:
        validate_speech_options(provider, voice)
        track_id = request.POST.get("track_id")
        if track_id is not None:
            track_id = preview_track_id(track_id)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    try:
        if track_id is None:
            task = generate_voice_preview.delay(provider, voice)
        else:
            task = generate_voice_preview.delay(provider, voice, track_id=track_id)
    except KombuError:
        logging.getLogger(__name__).exception("Could not queue voice preview")
        return JsonResponse({"error": "Could not start preview. Check the Celery broker."}, status=503)
    return JsonResponse({"task_id": task.id})


@require_http_methods(["GET", "POST"])
def utility_settings(request):
    settings, _ = UtilitySettings.objects.get_or_create(pk=1)
    section = request.POST.get("section", "voices") if request.method == "POST" else None
    if section not in (None, "voices", "shifts"):
        return HttpResponse("Invalid settings section.", status=400)
    data = request.POST if section == "voices" else None
    female_form = SpeechSelectionForm(data, prefix="female", gender="female", initial={
        "provider": settings.track_speech_provider, "voice": settings.track_speech_voice,
    })
    male_form = SpeechSelectionForm(data, prefix="male", gender="male", initial={
        "provider": settings.male_track_speech_provider, "voice": settings.male_track_speech_voice,
    })
    second_male_form = SpeechSelectionForm(data, prefix="male_2", gender="male", initial={
        "provider": settings.second_male_track_speech_provider,
        "voice": settings.second_male_track_speech_voice,
    })
    shift_form = AnnouncementShiftForm(
        request.POST if section == "shifts" else None,
        initial={
            "female_shift_start": settings.female_shift_start,
            "male_shift_start": settings.male_shift_start,
            "second_male_shift_start": settings.second_male_shift_start,
            "eighties_show_start": settings.eighties_show_start,
            "eighties_show_end": settings.eighties_show_end,
        },
    )
    if section == "voices":
        if all([female_form.is_valid(), male_form.is_valid(), second_male_form.is_valid()]):
            settings.track_speech_provider = female_form.cleaned_data["provider"]
            settings.track_speech_voice = female_form.cleaned_data["voice"]
            settings.male_track_speech_provider = male_form.cleaned_data["provider"]
            settings.male_track_speech_voice = male_form.cleaned_data["voice"]
            settings.second_male_track_speech_provider = second_male_form.cleaned_data["provider"]
            settings.second_male_track_speech_voice = second_male_form.cleaned_data["voice"]
            settings.save(update_fields=[
                "track_speech_provider", "track_speech_voice",
                "male_track_speech_provider", "male_track_speech_voice",
                "second_male_track_speech_provider", "second_male_track_speech_voice",
            ])
            return redirect(f"{reverse('utility-settings')}?saved=1")
    elif section == "shifts" and shift_form.is_valid():
        settings.female_shift_start = shift_form.cleaned_data["female_shift_start"]
        settings.male_shift_start = shift_form.cleaned_data["male_shift_start"]
        settings.second_male_shift_start = shift_form.cleaned_data["second_male_shift_start"]
        settings.eighties_show_start = shift_form.cleaned_data["eighties_show_start"]
        settings.eighties_show_end = shift_form.cleaned_data["eighties_show_end"]
        settings.save(update_fields=[
            "female_shift_start", "male_shift_start", "second_male_shift_start",
            "eighties_show_start", "eighties_show_end",
        ])
        return redirect(f"{reverse('utility-settings')}?saved=1")
    invalid_saved = False
    if section != "voices":
        try:
            validate_speech_options(settings.track_speech_provider, settings.track_speech_voice, gender="female")
            validate_speech_options(settings.male_track_speech_provider, settings.male_track_speech_voice, gender="male")
            validate_speech_options(settings.second_male_track_speech_provider, settings.second_male_track_speech_voice, gender="male")
        except ValueError:
            invalid_saved = True
    choices = {
        gender: {
            provider: [
                (voice, label) for voice, label in voices
                if SPEECH_VOICE_GENDERS[provider][voice] == gender
            ]
            for provider, voices in SPEECH_VOICES.items()
        }
        for gender in ("female", "male")
    }
    return render(request, "logviewer/utility_settings.html", {
        "female_form": female_form,
        "male_form": male_form,
        "second_male_form": second_male_form,
        "shift_form": shift_form,
        "gender_voice_choices": choices,
        "invalid_saved": invalid_saved,
        "saved": request.method == "GET" and request.GET.get("saved") == "1",
    })


@require_http_methods(["GET", "POST"])
def rds_override(request):
    state, _ = RdsDisplayState.objects.get_or_create(pk=1)

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "clear":
            state.override_text = None
            state.override_lifetime = RdsDisplayState.UNTIL_CLEARED
            state.save(update_fields=["override_text", "override_lifetime", "updated_at"])
            return redirect("rds-override")

        form = RdsOverrideForm(request.POST)
        if action == "set" and form.is_valid():
            state.override_text = form.cleaned_data["text"]
            state.override_lifetime = form.cleaned_data["lifetime"]
            state.save(update_fields=["override_text", "override_lifetime", "updated_at"])
            return redirect("rds-override")

        if action != "set":
            form.add_error(None, "Invalid action.")
    else:
        form = RdsOverrideForm(
            initial={
                "lifetime": (
                    state.override_lifetime
                    if state.override_text
                    else RdsDisplayState.UNTIL_NEXT_SONG
                )
            }
        )

    return render(
        request,
        "logviewer/rds_override.html",
        {"form": form, "state": state},
    )


@require_GET
def rds_display_status(request):
    state, _ = RdsDisplayState.objects.get_or_create(pk=1)
    return JsonResponse(
        {
            "current_text": state.current_text,
            "override_text": state.override_text,
            "override_lifetime": state.override_lifetime,
        }
    )


def voice_set_settings(settings, voice_set):
    if voice_set == "eighties":
        return EIGHTIES_SHOW_PROVIDER, EIGHTIES_SHOW_VOICE, "female"
    prefix, gender = {
        "female": ("track_speech_", "female"),
        "male": ("male_track_speech_", "male"),
        "male_2": ("second_male_track_speech_", "male"),
    }[voice_set]
    return getattr(settings, prefix + "provider"), getattr(settings, prefix + "voice"), gender


@require_POST
def start_single_audio(request, pk, num):
    track = get_object_or_404(Track, pk=pk)

    if num not in range(1, 17):
        return JsonResponse(
            {"error": "Invalid announcement number."},
            status=400,
        )

    if (num - 1) % 4 + 1 in (3, 4) and not track.yearid_id:
        return JsonResponse(
            {"error": f"Announcement {num} requires a track year."},
            status=400,
        )

    settings, _ = UtilitySettings.objects.get_or_create(pk=1)
    voice_set = ("female", "male", "male_2", "eighties")[(num - 1) // 4]
    provider, voice, gender = voice_set_settings(settings, voice_set)
    try:
        validate_speech_options(provider, voice, gender=gender)
    except ValueError:
        logging.getLogger(__name__).error("Invalid track speech settings")
        return JsonResponse(
            {"error": "Invalid track speech settings. Update them in Utilities > Settings."},
            status=503,
        )
    task = regenerate_single_audio.delay(
        track.pk, num, provider=provider, voice=voice,
    )

    return JsonResponse({"task_id": task.id})

@require_POST
def regenerate_artist_audio_start(request, pk):
    artist = get_object_or_404(Artist, pk=pk)
    form = ArtistAudioForm(
        request.POST,
        include_eighties=artist.track_set.filter(categoryid__category__icontains="80s").exists(),
    )
    if not form.is_valid():
        return JsonResponse({
            "error": "Select a voice set, at least one announcement variety, and a valid generation mode.",
        }, status=400)
    voice_set = form.cleaned_data["voice_set"]
    settings, _ = UtilitySettings.objects.get_or_create(pk=1)
    provider, voice, gender = voice_set_settings(settings, voice_set)
    try:
        validate_speech_options(provider, voice, gender=gender)
    except ValueError:
        logging.getLogger(__name__).error("Invalid artist speech settings")
        return JsonResponse({"error": "Invalid speech settings. Update them in Utilities > Settings."}, status=503)
    offset = {"female": 0, "male": 4, "male_2": 8, "eighties": 12}[voice_set]
    task = regenerate_artist_audio.delay(
        artist.pk, variants=[num + offset for num in form.cleaned_data["variants"]],
        provider=provider, voice=voice, mode=form.cleaned_data["mode"],
    )

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

class PerPageMixin:
    page_sizes = {
        "100": 100,
        "250": 250,
        "500": 500,
        "1000": 1000,
        "12000": 12000,
    }

    def get_table_pagination(self, table):
        pagination = super().get_table_pagination(table).copy()

        requested = self.request.GET.get("per_page", "250")
        pagination["per_page"] = self.page_sizes.get(requested, 250)

        return pagination

class FilteredPlaybackListView(PerPageMixin, ExportMixin, SingleTableMixin, FilterView):
    model = Playback
    table_class = PlaybackTable
    template_name = 'logviewer/playback.html'
    filterset_class = PlaybackFilter

    exclude_columns = ("announcements",)

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
        ).order_by("-date", "-timestamp")

    def get_table_kwargs(self):
        kwargs = super().get_table_kwargs()
        kwargs["request"] = self.request
        return kwargs
    
class FilteredTrackListView(PerPageMixin, ExportMixin, SingleTableMixin, FilterView):
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

class FilteredArtistListView(PerPageMixin, ExportMixin, SingleTableMixin, FilterView):
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
        ).order_by("artist_sort_name")

    def get_table_kwargs(self):
        kwargs = super().get_table_kwargs()
        kwargs["request"] = self.request
        return kwargs

class FilteredLogListView(PerPageMixin, ExportMixin, SingleTableMixin, FilterView):
    model = Log
    table_class = LogTable
    template_name = 'logviewer/log.html'

    table_pagination = {
        'per_page': 250,
        'paginator_class': LazyPaginator,
    }

    def get_queryset(self):
        return super().get_queryset().select_related(
            "trackid",
            "trackid__artistid",
        ).order_by("-date", "-timestamp")

    def get_table_kwargs(self):
        kwargs = super().get_table_kwargs()
        kwargs["request"] = self.request
        return kwargs

def announcement_file_statuses(track_id):
    records = {
        record.variant: record
        for record in AnnouncementVoice.objects.filter(track_id=track_id)
    }
    labels = (
        "artist + title", "title + artist", "year music", "title from year",
    )
    providers = dict(SPEECH_PROVIDERS)
    directory = announcement_path(track_id, 1).parent
    try:
        if not stat.S_ISDIR(directory.stat().st_mode):
            raise NotADirectoryError(str(directory))
        directory_available = True
    except OSError:
        logging.getLogger(__name__).warning(
            "Announcement directory is unavailable: %s", directory, exc_info=True,
        )
        directory_available = False

    statuses = []
    for num in range(1, 17):
        label = labels[(num - 1) % 4]
        display = "File status unavailable"
        if directory_available:
            path = announcement_path(track_id, num)
            try:
                exists = stat.S_ISREG(path.stat().st_mode)
            except FileNotFoundError:
                exists = False
            except OSError:
                logging.getLogger(__name__).warning(
                    "Could not check announcement file: %s", path, exc_info=True,
                )
                exists = None
            if exists is False:
                display = "File missing"
            elif exists:
                record = records.get(num)
                if record and record.provider:
                    provider = providers.get(record.provider, record.provider)
                    voices = dict(SPEECH_VOICES.get(record.provider, ()))
                    if record.provider == "microsoft":
                        voices.update(LEGACY_MICROSOFT_VOICES)
                    voice = voices.get(
                        record.voice, record.voice or "Unknown voice",
                    )
                    display = f"File exists: {provider} / {voice}"
                else:
                    display = "File exists: service and voice unknown"
        statuses.append({
            "number": num, "label": label, "display": display,
            "shift": f"Shift {(num - 1) // 4 + 1}" if num <= 12 else "80s show",
            "variety": (num - 1) % 4 + 1,
            "requires_year": (num - 1) % 4 + 1 in (3, 4),
        })
    return statuses


@require_GET
def track_announcement_status(request, pk):
    track = get_object_or_404(Track, pk=pk)
    return JsonResponse({"announcements": announcement_file_statuses(track.pk)})


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
        if regenerate1_clicked or regenerate2_clicked:
            settings, _ = UtilitySettings.objects.get_or_create(pk=1)
            validate_speech_options(
                settings.track_speech_provider, settings.track_speech_voice, gender="female",
            )
            title = speech_title(
                track.display_name, settings.track_speech_provider, settings.track_speech_voice,
            )
            if regenerate1_clicked:
                text = artist.display_name + ' with ' + title + ' on J-95!'
                num = 1
            else:
                text = title + ", that's " + artist.display_name + ' on K-V-W-J!'
                num = 2
            regenerate_audio(
                text, track.id, num,
                provider=settings.track_speech_provider, voice=settings.track_speech_voice,
            )
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
            "announcements": announcement_file_statuses(track.pk),
        },
    )

def artist_edit(request, pk):
    artist = get_object_or_404(Artist, pk=pk)

    tracks_list = artist.track_set.select_related("artistid", "categoryid", "yearid")
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
            "tracks_table": tracks_table,
            "audio_form": ArtistAudioForm(
                include_eighties=tracks_list.filter(categoryid__category__icontains="80s").exists(),
            ),
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