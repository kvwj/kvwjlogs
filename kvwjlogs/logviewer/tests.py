import json
import base64
from datetime import time
from importlib import import_module
from io import StringIO
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from xml.etree import ElementTree

import requests
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.test import SimpleTestCase
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call
from kombu.exceptions import OperationalError

from .models import RdsDisplayState, UtilitySettings
from .music_overlap import MUSIC_LIBRARY_FOLDERS, find_track_audio, scan_music_overlap
from .tasks import generate_category_audio, regenerate_single_audio, regenerate_artist_audio
from .forms import ArtistAudioForm, CategoryAudioForm
from .views import category_audio
from django.test import RequestFactory
from .models import Artist, Category, Track, Playback
from .tables import TracksTable, PlaybackTable
from .audio import regenerate_audio, synthesize_audio
from .models import AnnouncementVoice
from .tasks import generate_voice_preview
from .views import voice_preview, random_preview_track
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError
from django.apps import apps
from django.db import connection


class MusicOverlapTests(SimpleTestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for folder in MUSIC_LIBRARY_FOLDERS:
            (self.root / folder).mkdir(parents=True, exist_ok=True)
        self.tracks = []
        catalog_patch = patch("logviewer.views.Track.objects.select_related")
        catalog_patch.start().return_value.iterator.side_effect = lambda: iter(self.tracks)
        self.addCleanup(catalog_patch.stop)

    def add_file(self, relative, *, title=None, artist=None):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"unchanged audio")
        if title is None or artist is None:
            parts = path.stem.split(" - ", 1) if "80s" in path.parent.name.casefold() else path.stem.rsplit(" - ", 2)
            if "80s" in path.parent.name.casefold() and len(parts) == 2:
                artist, title = parts
            elif len(parts) == 3:
                title, artist, _ = parts
        if title is not None and artist is not None:
            self.tracks.append(Track(
                filename=path.name, track_name=title,
                artistid=Artist(pk=1, artist_name=artist),
                categoryid=Category(pk=1, category=path.parent.name),
            ))
        return str(path)

    def test_scan_matches_all_copies_by_title_artist_not_year(self):
        eighties = self.add_file("pls/That 80s Show/80s Rock/Artist - Title.mp3")
        other = self.add_file("ye olde new gold/playback/clockwheel rotation/A power oldie/ title  -  ARTIST  - 1985.mp3")
        another = self.add_file("pls/Day Only/Title - Artist - 1986.MP3")
        self.add_file("pls/Night only/Title - Different artist - 1985.mp3")
        self.add_file("pls/That 80s Show/80s Pop/Someone - Other song.mp3")
        self.add_file("pls/Day Only/Sunday/No artist.mp3")
        self.add_file("pls/Day Only/Special Liners/Station.mp3")
        result = scan_music_overlap(self.root, self.tracks)
        self.assertEqual(result["errors"], [])
        self.assertEqual(len(result["songs"]), 1)
        self.assertEqual(result["songs"][0]["eighties_paths"], [eighties])
        self.assertEqual(set(result["songs"][0]["other_paths"]), {other, another})
        self.assertEqual(Path(eighties).read_bytes(), b"unchanged audio")
        self.assertEqual(result["scanned"], 5)

    def test_bad_names_and_inaccessible_root_are_not_silent(self):
        bad = self.add_file("ye olde new gold/playback/clockwheel rotation/A power oldie/not a valid name.mp3")
        result = scan_music_overlap(self.root, self.tracks)
        self.assertEqual(result["errors"], [bad])
        with self.assertRaises(OSError):
            scan_music_overlap(self.root / "missing", self.tracks)
        with patch("logviewer.music_overlap.os.walk", side_effect=PermissionError("unreadable")):
            with self.assertRaises(PermissionError):
                scan_music_overlap(self.root, self.tracks)

    def test_page_and_csv_show_paths_and_report_failures(self):
        eighties = self.add_file("pls/That 80s Show/80s Rock/=Artist - Title.mp3")
        other = self.add_file("ye olde new gold/playback/clockwheel rotation/A power oldie/Title - =Artist - 1985.mp3")
        with self.settings(MUSIC_LIBRARY_ROOT=str(self.root)):
            response = self.client.get(reverse("music-overlap-report"))
            self.assertContains(response, "Scan and show report")
            response = self.client.post(reverse("music-overlap-report"), {"format": "html"})
            self.assertContains(response, eighties)
            self.assertContains(response, other)
            response = self.client.post(reverse("music-overlap-report"), {"format": "csv"})
            self.assertEqual(response.status_code, 200)
            self.assertIn("attachment", response["Content-Disposition"])
            self.assertIn("'=Artist", response.content.decode())
            self.add_file("ye olde new gold/playback/clockwheel rotation/A power oldie/malformed.mp3")
            response = self.client.post(reverse("music-overlap-report"), {"format": "csv"})
            self.assertEqual(response.status_code, 422)
            self.assertContains(response, "report is incomplete", status_code=422)
        with self.settings(MUSIC_LIBRARY_ROOT=str(self.root / "missing")), self.assertLogs(
            "logviewer.views", level="ERROR",
        ):
            response = self.client.post(reverse("music-overlap-report"))
        self.assertEqual(response.status_code, 503)

    def test_unlisted_folders_are_not_traversed_or_reported(self):
        self.add_file("ye olde new gold/playback/clockwheel rotation/A power oldie/Title - Artist - 1985.mp3")
        self.add_file("pls/That 80s Show/80s Rock/Artist - Title.mp3")
        excluded = (
            "backups/80s Rock/Artist - Title.mp3",
            "ye olde new gold/unlisted/malformed.mp3",
            "ye olde new gold/A power oldie/malformed.mp3",
            "pls/unused/malformed.mp3",
        )
        for path in excluded:
            self.add_file(path)
        with patch("logviewer.music_overlap.os.walk", wraps=os.walk) as walk:
            result = scan_music_overlap(self.root, self.tracks)
        self.assertEqual(
            [call.args[0] for call in walk.call_args_list],
            [self.root / folder for folder in MUSIC_LIBRARY_FOLDERS],
        )
        self.assertEqual(result["scanned"], 2)
        self.assertEqual(result["errors"], [])
        self.assertEqual(len(result["songs"]), 1)
        self.assertEqual(len(result["songs"][0]["eighties_paths"]), 1)

    def test_missing_approved_folder_fails_instead_of_returning_partial_results(self):
        missing = self.root / "ye olde new gold/playback/clockwheel rotation/K christmas-holiday"
        missing.rmdir()
        with self.assertRaisesMessage(FileNotFoundError, str(missing)):
            scan_music_overlap(self.root, self.tracks)

    def test_display_names_match_mono_filename_and_ignore_stale_catalog_rows(self):
        eighties = self.add_file(
            "pls/That 80s Show/80s CHR/Eurythmics - Here Comes The Rain Again.mp3",
            title="Here Comes The Rain Again", artist="Eurythmics",
        )
        regular = self.add_file(
            "pls/Night only/Here Comes The Rain Again (mono) - Eurythmics - 1983.mp3",
            title="Here Comes The Rain Again (mono)", artist="Eurythmics original artist",
        )
        self.tracks[-1].track_display_name = " Here Comes the Rain Again "
        self.tracks[-1].artistid.artist_display_name = " EURYTHMICS "
        with self.settings(MUSIC_LIBRARY_ROOT=str(self.root)):
            response = self.client.post(reverse("music-overlap-report"), {"format": "html"})
            self.assertContains(response, eighties)
            self.assertContains(response, regular)
            response = self.client.post(reverse("music-overlap-report"), {"format": "csv"})
            self.assertEqual(response.status_code, 200)
            self.assertIn(regular, response.content.decode())
        result = scan_music_overlap(self.root, self.tracks)
        self.assertEqual(result["errors"], [])
        self.assertEqual(len(result["songs"]), 1)
        self.assertEqual(result["songs"][0]["eighties_paths"], [eighties])
        self.assertEqual(result["songs"][0]["other_paths"], [regular])
        Path(regular).unlink()
        self.assertEqual(scan_music_overlap(self.root, self.tracks)["songs"], [])

    def test_files_without_catalog_rows_still_match_but_ambiguous_rows_are_reported(self):
        path = self.add_file("pls/That 80s Show/80s CHR/Eurythmics - Here Comes The Rain Again.mp3")
        regular = self.add_file(
            "pls/Night only/Here Comes The Rain Again - Eurythmics - 1983.mp3",
        )
        self.tracks.clear()
        result = scan_music_overlap(self.root, self.tracks)
        self.assertEqual(result["errors"], [])
        self.assertEqual(len(result["songs"]), 1)
        self.assertEqual(result["songs"][0]["eighties_paths"], [path])
        self.assertEqual(result["songs"][0]["other_paths"], [regular])
        self.add_file("pls/That 80s Show/80s CHR/Eurythmics - Here Comes The Rain Again.mp3")
        self.add_file("pls/Night only/Here Comes The Rain Again - Eurythmics - 1983.mp3")
        duplicate = Track(
            filename=Path(regular).name, track_name="Different song",
            artistid=Artist(pk=2, artist_name="Eurythmics"),
            categoryid=Category(pk=2, category="Night only"),
        )
        self.tracks.append(duplicate)
        result = scan_music_overlap(self.root, self.tracks)
        self.assertEqual(result["errors"], [regular])
        self.assertEqual(result["songs"], [])

    def test_missing_filenames_use_original_names_to_find_display_names(self):
        eighties = self.add_file(
            "pls/That 80s Show/80s CHR/Eurythmics - Here Comes The Rain Again.mp3",
        )
        regular = self.add_file(
            "pls/Night only/Here Comes The Rain Again (mono) - Eurythmics - 1983.mp3",
        )
        self.tracks[-1].track_display_name = "Here Comes the Rain Again"
        for filenames in ((None, ""), (None, Path(regular).name), (Path(eighties).name, None)):
            with self.subTest(filenames=filenames):
                for track, filename in zip(self.tracks, filenames):
                    track.filename = filename
                result = scan_music_overlap(self.root, self.tracks)
                self.assertEqual(result["errors"], [])
                self.assertEqual(len(result["songs"]), 1)
                self.assertEqual(result["songs"][0]["eighties_paths"], [eighties])
                self.assertEqual(result["songs"][0]["other_paths"], [regular])
        with self.settings(MUSIC_LIBRARY_ROOT=str(self.root)):
            response = self.client.post(reverse("music-overlap-report"), {"format": "csv"})
        self.assertEqual(response.status_code, 200)
        self.assertIn(regular, response.content.decode())
        self.assertIsNone(self.tracks[-1].filename)

    def test_conflicting_name_matches_without_filenames_are_not_guessed(self):
        regular = self.add_file("pls/Night only/Title - Artist - 1983.mp3")
        self.tracks[0].filename = None
        self.tracks.append(Track(
            track_name="Title", track_display_name="Different title",
            artistid=self.tracks[0].artistid, categoryid=self.tracks[0].categoryid,
        ))
        result = scan_music_overlap(self.root, self.tracks)
        self.assertEqual(result["errors"], [regular])

    def test_keeps_previous_six_and_three_display_name_matches_with_blank_filenames(self):
        examples = (
            ("Big Country", "In A Big Country", "In A Big Country", "In A Big Country"),
            ("Blue Oyster Cult", "Burnin' for You", "Burnin' for You", "Burnin' for You"),
            ("Cars", "Just What I Needed ", "Just What I Needed", "Just What I Needed"),
            ("Cars", "Let's Go ", "Let's Go", "Let's Go"),
            ("Eddie Money", "Baby Hold On", "Baby Hold On", "Baby Hold On"),
            ("Elton John", "Empty Garden", "Empty Garden", "Empty Garden"),
            ("Agnetha Faltskog", "Can't Shake Loose", "Can't Shake Loose (mono)", "Can't Shake Loose"),
            ("Eurythmics", "Here Comes The Rain Again", "Here Comes The Rain Again (mono)", "Here Comes the Rain Again"),
            ("Cars", "Candy-0", "Candy-O", "Candy-O"),
        )
        expected = set()
        for artist, eighties_title, other_title, display_title in examples:
            eighties = self.add_file(f"pls/That 80s Show/80s CHR/{artist} - {eighties_title}.mp3")
            other_folder = (
                "ye olde new gold/playback/clockwheel rotation/F image track"
                if artist == "Elton John" else "pls/Night only"
            )
            regular = self.add_file(f"{other_folder}/{other_title} - {artist} - 1983.mp3")
            for track, filename in zip(self.tracks[-2:], (None, "")):
                track.filename = filename
                track.track_display_name = display_title
                if artist == "Cars":
                    track.artistid.artist_display_name = "The Cars"
            expected.add((eighties, regular))
        result = scan_music_overlap(self.root, self.tracks)
        self.assertEqual(result["errors"], [])
        self.assertEqual(len(result["songs"]), 9)
        self.assertEqual({
            (song["eighties_paths"][0], song["other_paths"][0])
            for song in result["songs"]
        }, expected)


class TrackAudioTests(SimpleTestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for folder in MUSIC_LIBRARY_FOLDERS:
            (self.root / folder).mkdir(parents=True, exist_ok=True)
        self.track = Track(
            pk=42, track_name="Title", filename="Artist - Title.mp3",
            categoryid=Category(pk=1, category="80s Rock"),
        )
        self.path = self.root / "pls" / "That 80s Show" / "80s Rock" / self.track.filename
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(b"0123456789")
        self.url = reverse("track-audio", args=[self.track.pk])
        self.lookup = patch("logviewer.views.get_object_or_404", return_value=self.track)
        self.lookup.start()
        self.addCleanup(self.lookup.stop)
        self.setting = self.settings(MUSIC_LIBRARY_ROOT=str(self.root))
        self.setting.enable()
        self.addCleanup(self.setting.disable)

    def test_full_stream_head_and_seek_ranges(self):
        response = self.client.get(self.url)
        self.addCleanup(response.close)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "audio/mpeg")
        self.assertEqual(response["Content-Length"], "10")
        self.assertEqual(b"".join(response.streaming_content), b"0123456789")
        response = self.client.head(self.url)
        self.assertEqual(response["Content-Length"], "10")
        self.assertEqual(response.content, b"")
        for requested, expected, content_range in (
            ("bytes=2-5", b"2345", "bytes 2-5/10"),
            ("bytes=7-", b"789", "bytes 7-9/10"),
            ("bytes=-3", b"789", "bytes 7-9/10"),
            ("bytes=8-99", b"89", "bytes 8-9/10"),
        ):
            with self.subTest(requested=requested):
                response = self.client.get(self.url, HTTP_RANGE=requested)
                self.addCleanup(response.close)
                self.assertEqual(response.status_code, 206)
                self.assertEqual(response["Content-Range"], content_range)
                self.assertEqual(int(response["Content-Length"]), len(expected))
                self.assertEqual(b"".join(response.streaming_content), expected)
        for requested in ("bytes=10-", "bytes=6-2", "bytes=-0", "bytes=-", "bytes=0-1,4-5"):
            response = self.client.get(self.url, HTTP_RANGE=requested)
            self.assertEqual(response.status_code, 416, requested)
            self.assertEqual(response["Content-Range"], "bytes */10")
        self.assertEqual(self.path.read_bytes(), b"0123456789")

    def test_missing_ambiguous_and_unreadable_files_surface_errors(self):
        self.path.unlink()
        with self.assertLogs("logviewer.views", level="WARNING"):
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 404)
        self.path.write_bytes(b"audio")
        other = self.root / "pls" / "That 80s Show" / "another" / "80s Rock" / self.track.filename
        other.parent.mkdir(parents=True)
        other.write_bytes(b"other")
        with self.assertLogs("logviewer.views", level="WARNING"):
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 409)
        with patch("logviewer.views.find_track_audio", side_effect=PermissionError), self.assertLogs(
            "logviewer.views", level="ERROR",
        ):
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 503)

    def test_matching_is_category_specific_and_confined_to_library(self):
        excluded = self.root / "backups" / "80s Rock" / self.track.filename
        excluded.parent.mkdir(parents=True)
        excluded.write_bytes(b"wrong recording")
        self.assertEqual(find_track_audio(self.root, "ARTIST - TITLE.MP3", "80S ROCK"), self.path.resolve())
        with self.assertRaises(FileNotFoundError):
            find_track_audio(self.root, self.track.filename, "Other category")
        with self.assertRaises(ValueError):
            find_track_audio(self.root, "../Artist - Title.mp3", "80s Rock")
        outside = TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        target = Path(outside.name) / "outside.mp3"
        target.write_bytes(b"private")
        self.path.unlink()
        self.path.symlink_to(target)
        with self.assertRaises(PermissionError):
            find_track_audio(self.root, self.track.filename, "80s Rock")

    def test_play_button_keeps_export_clean_and_never_preloads_audio(self):
        table = TracksTable([self.track])
        markup = table.render_track_name(self.track)
        self.assertIn("Play song", markup)
        self.assertIn(self.url, markup)
        self.assertIn('preload="none"', markup)
        self.assertEqual(table.value_track_name(self.track), "Title")
        self.track.filename = None
        markup = table.render_track_name(self.track)
        self.assertNotIn("Play song", markup)
        self.assertIn("No song filename", markup)


class PlaybackCategoryTests(SimpleTestCase):
    def test_playback_table_uses_snapshot_and_marks_inferred_history(self):
        from .models import PlaybackCategory

        track = Track(pk=42, categoryid=Category(pk=1, category="New category"))
        record = Playback(pk=100, trackid=track)
        record.playback_category = "Original category"
        record.category_snapshot = PlaybackCategory(
            playback=record, category="Original category", inferred=False,
        )
        table = PlaybackTable([record])
        self.assertEqual(table.render_category(record), "Original category")
        record.category_snapshot.inferred = True
        self.assertEqual(table.render_category(record), "Original category (inferred)")


class PlaybackCategoryQueryTests(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from .models import Year
        cls.catalog_models = (Year, Category, Artist, Track, Playback)
        with connection.schema_editor() as editor:
            for model in cls.catalog_models:
                editor.create_model(model)

    @classmethod
    def tearDownClass(cls):
        try:
            with connection.schema_editor() as editor:
                for model in reversed(cls.catalog_models):
                    editor.delete_model(model)
        finally:
            super().tearDownClass()

    def test_view_and_filter_keep_event_category_when_track_moves(self):
        from .models import PlaybackCategory
        from .views import FilteredPlaybackListView
        from .filters import PlaybackFilter

        original = Category.objects.create(category="Original")
        new = Category.objects.create(category="New")
        track = Track.objects.create(track_name="Title", categoryid=original)
        captured = Playback.objects.create(trackid=track, date="2026-11-19", timestamp="01:00:00")
        PlaybackCategory.objects.create(playback=captured, category="Original")
        legacy = Playback.objects.create(trackid=track, date="2026-11-19", timestamp="02:00:00")
        track.categoryid = new
        track.save()
        queryset = FilteredPlaybackListView().get_queryset()
        records = {record.pk: record for record in queryset}
        self.assertEqual(records[captured.pk].playback_category, "Original")
        self.assertEqual(records[legacy.pk].playback_category, "New")
        self.assertEqual(PlaybackTable([records[legacy.pk]]).render_category(records[legacy.pk]), "New (inferred)")
        self.assertEqual(
            list(PlaybackFilter({"category": "Original"}, queryset=queryset).qs.values_list("pk", flat=True)),
            [captured.pk],
        )
        self.assertEqual(
            list(PlaybackFilter({"category": "New"}, queryset=queryset).qs.values_list("pk", flat=True)),
            [legacy.pk],
        )


class RdsOverrideTests(TestCase):
    def setUp(self):
        self.state = RdsDisplayState.objects.create(
            pk=1,
            current_text="Current track",
        )
        self.url = reverse("rds-override")

    def test_override_page_shows_current_display(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Current track")
        self.assertContains(response, "Until the next playback update")
        self.assertContains(response, "Until cleared manually")

    def test_setting_override_saves_text_and_lifetime(self):
        response = self.client.post(
            self.url,
            {
                "action": "set",
                "text": "Engineer message",
                "lifetime": RdsDisplayState.UNTIL_CLEARED,
            },
        )

        self.assertRedirects(response, self.url)
        self.state.refresh_from_db()
        self.assertEqual(self.state.override_text, "Engineer message")
        self.assertEqual(
            self.state.override_lifetime,
            RdsDisplayState.UNTIL_CLEARED,
        )

    def test_clear_override_removes_override(self):
        self.state.override_text = "Engineer message"
        self.state.save()

        response = self.client.post(self.url, {"action": "clear"})

        self.assertRedirects(response, self.url)
        self.state.refresh_from_db()
        self.assertIsNone(self.state.override_text)

    def test_override_rejects_text_over_rds_limit(self):
        response = self.client.post(
            self.url,
            {
                "action": "set",
                "text": "x" * 65,
                "lifetime": RdsDisplayState.UNTIL_NEXT_SONG,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.state.refresh_from_db()
        self.assertIsNone(self.state.override_text)

    def test_override_rejects_line_breaks(self):
        response = self.client.post(
            self.url,
            {
                "action": "set",
                "text": "first line\nTEXT=another command",
                "lifetime": RdsDisplayState.UNTIL_NEXT_SONG,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.state.refresh_from_db()
        self.assertIsNone(self.state.override_text)

    def test_status_endpoint_returns_current_display_and_override(self):
        self.state.override_text = "Engineer message"
        self.state.override_lifetime = RdsDisplayState.UNTIL_NEXT_SONG
        self.state.save()

        response = self.client.get(reverse("rds-display-status"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "current_text": "Current track",
                "override_text": "Engineer message",
                "override_lifetime": RdsDisplayState.UNTIL_NEXT_SONG,
            },
        )


class UtilitySettingsTests(TestCase):
    def test_shift_times_are_saved_independently_of_voice_settings(self):
        settings = UtilitySettings.objects.get(pk=1)
        self.assertEqual((settings.eighties_show_start, settings.eighties_show_end), (time(21), time(0)))
        self.assertEqual(
            (settings.female_shift_start, settings.male_shift_start, settings.second_male_shift_start),
            (time(6), time(12), time(19)),
        )
        self.assertContains(self.client.get(reverse("utility-settings")), 'value="06:00"')
        response = self.client.post(reverse("utility-settings"), {
            "section": "shifts", "female_shift_start": "20:30", "male_shift_start": "07:15",
            "second_male_shift_start": "12:00",
            "eighties_show_start": "22:15", "eighties_show_end": "01:30",
        })
        self.assertRedirects(response, reverse("utility-settings") + "?saved=1")
        settings.refresh_from_db()
        self.assertEqual((settings.female_shift_start, settings.male_shift_start), (time(20, 30), time(7, 15)))
        self.assertEqual(settings.second_male_shift_start, time(12))
        self.assertEqual((settings.eighties_show_start, settings.eighties_show_end), (time(22, 15), time(1, 30)))
        self.assertEqual((settings.track_speech_provider, settings.track_speech_voice), ("microsoft", "nova_dragon_hd"))

    def test_invalid_or_equal_shift_times_are_not_saved(self):
        for female, male, third, error in (
            ("08:00", "08:00", "19:00", "The three shifts must start at different times."),
            ("08:00", "12:00", "08:00", "The three shifts must start at different times."),
            ("25:00", "12:00", "19:00", "Enter a valid time."),
            ("", "12:00", "19:00", "This field is required."),
        ):
            with self.subTest(female=female, male=male, third=third):
                response = self.client.post(reverse("utility-settings"), {
                    "section": "shifts", "female_shift_start": female, "male_shift_start": male,
                    "second_male_shift_start": third,
                    "eighties_show_start": "21:00", "eighties_show_end": "00:00",
                })
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, error)
                settings = UtilitySettings.objects.get(pk=1)
                self.assertEqual(
                    (settings.female_shift_start, settings.male_shift_start, settings.second_male_shift_start),
                    (time(6), time(12), time(19)),
                )

    def test_equal_or_missing_show_times_are_not_saved(self):
        for start, end, error in (
            ("21:00", "21:00", "The 80s show must start and end at different times."),
            ("", "00:00", "This field is required."),
        ):
            response = self.client.post(reverse("utility-settings"), {
                "section": "shifts", "female_shift_start": "06:00", "male_shift_start": "12:00",
                "second_male_shift_start": "19:00", "eighties_show_start": start, "eighties_show_end": end,
            })
            self.assertContains(response, error)
            settings = UtilitySettings.objects.get(pk=1)
            self.assertEqual((settings.eighties_show_start, settings.eighties_show_end), (time(21), time(0)))

    def test_dragonhd_defaults_and_shared_selection_persist(self):
        response = self.client.get(reverse("utility-settings"))
        self.assertContains(response, "Track and artist announcement regeneration")
        settings = UtilitySettings.objects.get(pk=1)
        self.assertEqual((settings.track_speech_provider, settings.track_speech_voice), ("microsoft", "nova_dragon_hd"))
        self.assertEqual((settings.male_track_speech_provider, settings.male_track_speech_voice), ("microsoft", "andrew_dragon_hd"))
        self.assertEqual((settings.second_male_track_speech_provider, settings.second_male_track_speech_voice), ("microsoft", "davis_dragon_hd"))

        response = self.client.post(
            reverse("utility-settings"), {
                "female-provider": "microsoft", "female-voice": "ava_dragon_hd",
                "male-provider": "microsoft", "male-voice": "brian_dragon_hd",
                "male_2-provider": "openai", "male_2-voice": "ash",
            },
        )
        self.assertRedirects(response, reverse("utility-settings") + "?saved=1")
        settings.refresh_from_db()
        self.assertEqual((settings.track_speech_provider, settings.track_speech_voice), ("microsoft", "ava_dragon_hd"))
        self.assertEqual((settings.male_track_speech_provider, settings.male_track_speech_voice), ("microsoft", "brian_dragon_hd"))
        self.assertEqual((settings.second_male_track_speech_provider, settings.second_male_track_speech_voice), ("openai", "ash"))
        response = self.client.get(reverse("utility-settings"))
        self.assertContains(response, '<option value="brian_dragon_hd" selected>Brian DragonHD (male)</option>', html=True)

    def test_settings_initialization_preserves_existing_voice(self):
        create_settings = import_module("logviewer.migrations.0007_announcement_shift_times").create_settings
        settings = UtilitySettings.objects.get(pk=1)
        settings.track_speech_provider = "openai"
        settings.track_speech_voice = "nova"
        settings.save(update_fields=["track_speech_provider", "track_speech_voice"])
        create_settings(apps, SimpleNamespace(connection=connection))
        settings.refresh_from_db()
        self.assertEqual((settings.track_speech_provider, settings.track_speech_voice), ("openai", "nova"))
        settings.delete()
        create_settings(apps, SimpleNamespace(connection=connection))
        settings = UtilitySettings.objects.get(pk=1)
        self.assertEqual((settings.track_speech_provider, settings.track_speech_voice), ("microsoft", "nova_dragon_hd"))

    def test_third_shift_migration_replaces_schedule_but_preserves_voices_and_metadata(self):
        migrate = import_module("logviewer.migrations.0010_third_announcement_shift").update_shift_times
        settings = UtilitySettings.objects.get(pk=1)
        settings.female_shift_start = time(4)
        settings.male_shift_start = time(15)
        settings.track_speech_voice = "ava_dragon_hd"
        settings.male_track_speech_voice = "brian_dragon_hd"
        settings.save()
        metadata = AnnouncementVoice.objects.create(track_id=42, variant=5, provider="openai", voice="ash")
        migrate(apps, SimpleNamespace(connection=connection))
        settings.refresh_from_db()
        self.assertEqual(
            (settings.female_shift_start, settings.male_shift_start, settings.second_male_shift_start),
            (time(6), time(12), time(19)),
        )
        self.assertEqual((settings.track_speech_voice, settings.male_track_speech_voice), ("ava_dragon_hd", "brian_dragon_hd"))
        metadata.refresh_from_db()
        self.assertEqual((metadata.provider, metadata.voice), ("openai", "ash"))

    def test_existing_openai_nova_setting_is_migrated_without_changing_other_data(self):
        migrate = import_module("logviewer.migrations.0009_update_female_nova_setting").update_female_nova_setting
        schema_editor = SimpleNamespace(connection=connection)
        settings = UtilitySettings.objects.get(pk=1)
        metadata = AnnouncementVoice.objects.create(track_id=42, variant=1, provider="openai", voice="nova")
        for provider, voice, expected in (
            ("openai", "nova", ("microsoft", "nova_dragon_hd")),
            ("openai", "ash", ("openai", "ash")),
            ("microsoft", "ava_dragon_hd", ("microsoft", "ava_dragon_hd")),
        ):
            with self.subTest(provider=provider, voice=voice):
                settings.track_speech_provider = provider
                settings.track_speech_voice = voice
                settings.male_shift_start = time(19)
                settings.save()
                migrate(apps, schema_editor)
                settings.refresh_from_db()
                self.assertEqual((settings.track_speech_provider, settings.track_speech_voice), expected)
                self.assertEqual(settings.male_shift_start, time(19))
                self.assertEqual((settings.male_track_speech_provider, settings.male_track_speech_voice), ("microsoft", "andrew_dragon_hd"))
        metadata.refresh_from_db()
        self.assertEqual((metadata.provider, metadata.voice), ("openai", "nova"))

    def test_invalid_provider_voice_pair_does_not_change_settings(self):
        settings, _ = UtilitySettings.objects.update_or_create(
            pk=1, defaults={"track_speech_provider": "microsoft", "track_speech_voice": "ava_dragon_hd"},
        )
        response = self.client.post(
            reverse("utility-settings"), {
                "female-provider": "microsoft", "female-voice": "brian_dragon_hd",
                "male-provider": "openai", "male-voice": "ash",
                "male_2-provider": "microsoft", "male_2-voice": "davis_dragon_hd",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Select a valid choice")
        settings.refresh_from_db()
        self.assertEqual((settings.track_speech_provider, settings.track_speech_voice), ("microsoft", "ava_dragon_hd"))

    @patch("logviewer.views.get_object_or_404")
    @patch("logviewer.views.regenerate_single_audio.delay")
    def test_queued_task_uses_settings_at_submission(self, delay, get_track):
        settings, _ = UtilitySettings.objects.update_or_create(
            pk=1, defaults={"male_track_speech_provider": "microsoft", "male_track_speech_voice": "andrew_dragon_hd"},
        )
        get_track.return_value = SimpleNamespace(pk=42, yearid_id=1966)
        delay.return_value.id = "task-id"
        response = self.client.post(reverse("start-single-audio", args=[42, 7]))
        self.assertEqual(response.status_code, 200)
        settings.male_track_speech_voice = "brian_dragon_hd"
        settings.save(update_fields=["male_track_speech_voice"])
        delay.assert_called_once_with(42, 7, provider="microsoft", voice="andrew_dragon_hd")

    @patch("logviewer.views.get_object_or_404")
    @patch("logviewer.views.regenerate_single_audio.delay")
    def test_invalid_saved_settings_cannot_queue_generation(self, delay, get_track):
        UtilitySettings.objects.update_or_create(
            pk=1, defaults={"track_speech_provider": "microsoft", "track_speech_voice": "ash"},
        )
        get_track.return_value = SimpleNamespace(pk=42, yearid_id=1966)
        with self.assertLogs("logviewer.views", level="ERROR"):
            response = self.client.post(reverse("start-single-audio", args=[42, 1]))
        self.assertEqual(response.status_code, 503)
        self.assertIn("Invalid track speech settings", response.json()["error"])
        delay.assert_not_called()

    @patch("logviewer.views.get_object_or_404")
    @patch("logviewer.views.regenerate_audio")
    def test_legacy_track_regeneration_uses_settings(self, regenerate, get_track):
        UtilitySettings.objects.update_or_create(
            pk=1, defaults={"track_speech_provider": "microsoft", "track_speech_voice": "emma_dragon_hd"},
        )
        get_track.return_value = SimpleNamespace(
            pk=42, id=42, display_name="London Is Behind Me",
            artistid=SimpleNamespace(display_name="The Moody Blues"),
        )
        response = self.client.post(reverse("track-artist-edit", args=[42]), {"regenerate_audio_1": "1"})
        self.assertEqual(response.status_code, 302)
        regenerate.assert_called_once_with(
            "The Moody Blues with London Is Behind Me on J-95!",
            42, 1, provider="microsoft", voice="emma_dragon_hd",
        )

    def test_saved_settings_reject_wrong_gender_and_warn_on_existing_invalid_voice(self):
        settings, _ = UtilitySettings.objects.update_or_create(
            pk=1, defaults={"track_speech_provider": "openai", "track_speech_voice": "ash"},
        )
        response = self.client.get(reverse("utility-settings"))
        self.assertContains(response, "A saved voice does not match its announcement set")
        response = self.client.post(reverse("utility-settings"), {
            "female-provider": "openai", "female-voice": "nova",
            "male-provider": "microsoft", "male-voice": "emma_dragon_hd",
            "male_2-provider": "microsoft", "male_2-voice": "nova_dragon_hd",
        })
        self.assertContains(response, "Select a valid choice")
        settings.refresh_from_db()
        self.assertEqual(settings.track_speech_voice, "ash")
        self.assertTrue(response.context["second_male_form"].errors)

    @patch("logviewer.views.get_object_or_404")
    @patch("logviewer.views.regenerate_artist_audio.delay")
    def test_artist_queue_uses_selected_set_varieties_and_saved_voice(self, delay, get_artist):
        UtilitySettings.objects.update_or_create(
            pk=1, defaults={"male_track_speech_provider": "openai", "male_track_speech_voice": "ash"},
        )
        get_artist.return_value = SimpleNamespace(pk=7, track_set=MagicMock())
        get_artist.return_value.track_set.filter.return_value.exists.return_value = False
        delay.return_value.id = "task-id"
        for mode in ("missing", "regenerate"):
            with self.subTest(mode=mode):
                response = self.client.post(reverse("regenerate-artist-audio-start", args=[7]), {
                    "voice_set": "male", "variants": [1, 3, 4], "mode": mode,
                })
                self.assertEqual(response.status_code, 200)
                delay.assert_called_once_with(
                    7, variants=[5, 7, 8], provider="openai", voice="ash", mode=mode,
                )
                delay.reset_mock()
        for overrides in ({"variants": [5]}, {"mode": "bad"}, {"mode": ""}):
            with self.subTest(overrides=overrides):
                response = self.client.post(reverse("regenerate-artist-audio-start", args=[7]), {
                    "voice_set": "male", "variants": [1], "mode": "regenerate", **overrides,
                })
                self.assertEqual(response.status_code, 400)
                delay.assert_not_called()

    @patch("logviewer.views.get_object_or_404")
    @patch("logviewer.views.regenerate_single_audio.delay")
    @patch("logviewer.views.regenerate_artist_audio.delay")
    def test_third_shift_queues_track_and_artist_with_its_saved_voice(self, artist_delay, single_delay, get_object):
        get_object.return_value = SimpleNamespace(pk=42, yearid_id=1966, track_set=MagicMock())
        get_object.return_value.track_set.filter.return_value.exists.return_value = False
        single_delay.return_value.id = artist_delay.return_value.id = "task-id"
        response = self.client.post(reverse("start-single-audio", args=[42, 12]))
        self.assertEqual(response.status_code, 200)
        single_delay.assert_called_once_with(42, 12, provider="microsoft", voice="davis_dragon_hd")
        response = self.client.post(reverse("regenerate-artist-audio-start", args=[42]), {
            "voice_set": "male_2", "variants": [1, 3, 4], "mode": "regenerate",
        })
        self.assertEqual(response.status_code, 200)
        artist_delay.assert_called_once_with(
            42, variants=[9, 11, 12], provider="microsoft", voice="davis_dragon_hd", mode="regenerate",
        )

    @patch("logviewer.views.get_object_or_404")
    @patch("logviewer.views.regenerate_single_audio.delay")
    @patch("logviewer.views.regenerate_artist_audio.delay")
    def test_eighties_queue_always_uses_emma(self, artist_delay, single_delay, get_object):
        get_object.return_value = SimpleNamespace(pk=42, yearid_id=1966, track_set=MagicMock())
        get_object.return_value.track_set.filter.return_value.exists.return_value = True
        single_delay.return_value.id = artist_delay.return_value.id = "task-id"
        response = self.client.post(reverse("start-single-audio", args=[42, 16]))
        self.assertEqual(response.status_code, 200)
        single_delay.assert_called_once_with(42, 16, provider="microsoft", voice="emma_dragon_hd")
        response = self.client.post(reverse("regenerate-artist-audio-start", args=[42]), {
            "voice_set": "eighties", "variants": [1, 3, 4], "mode": "missing",
        })
        self.assertEqual(response.status_code, 200)
        artist_delay.assert_called_once_with(
            42, variants=[13, 15, 16], provider="microsoft", voice="emma_dragon_hd", mode="missing",
        )
        artist_delay.reset_mock()
        get_object.return_value.track_set.filter.return_value.exists.return_value = False
        response = self.client.post(reverse("regenerate-artist-audio-start", args=[42]), {
            "voice_set": "eighties", "variants": [1], "mode": "missing",
        })
        self.assertEqual(response.status_code, 400)
        artist_delay.assert_not_called()

    def test_dragonhd_migration_only_replaces_recognized_microsoft_settings(self):
        migrate = import_module("logviewer.migrations.0005_dragonhd_track_setting").migrate_track_speech_setting
        schema_editor = SimpleNamespace(connection=connection)
        metadata = AnnouncementVoice.objects.create(
            track_id=42, variant=1, provider="microsoft", voice="nova",
        )
        for provider, voice, expected in (
            *(("microsoft", voice, "nova_dragon_hd") for voice in (
                "nova", "shimmer", "echo", "christopher", "onyx",
            )),
            ("microsoft", "unknown", "unknown"),
            ("microsoft", "ava_dragon_hd", "ava_dragon_hd"),
            ("openai", "nova", "nova"),
            ("openai", "ash", "ash"),
        ):
            with self.subTest(provider=provider, voice=voice):
                settings, _ = UtilitySettings.objects.update_or_create(
                    pk=1, defaults={"track_speech_provider": provider, "track_speech_voice": voice},
                )
                migrate(apps, schema_editor)
                settings.refresh_from_db()
                self.assertEqual((settings.track_speech_provider, settings.track_speech_voice), (provider, expected))
        metadata.refresh_from_db()
        self.assertEqual((metadata.provider, metadata.voice), ("microsoft", "nova"))


class CategoryAudioTests(SimpleTestCase):
    def setUp(self):
        self.tracks_patch = patch("logviewer.tasks.Track.objects")
        tracks = self.tracks_patch.start()
        self.addCleanup(self.tracks_patch.stop)
        self.queryset = tracks.filter.return_value.select_related.return_value.order_by.return_value
        self.category_patch = patch("logviewer.tasks.Category.objects.get")
        self.category_patch.start()
        self.addCleanup(self.category_patch.stop)
        self.state_patch = patch.object(generate_category_audio, "update_state")
        self.state_patch.start()
        self.addCleanup(self.state_patch.stop)
        self.audio_patch = patch("logviewer.tasks.regenerate_audio")
        self.audio = self.audio_patch.start()
        self.addCleanup(self.audio_patch.stop)
        self.path_patch = patch("logviewer.tasks.announcement_path")
        self.path = self.path_patch.start()
        self.path.return_value.is_file.return_value = False
        self.addCleanup(self.path_patch.stop)
        self.track = SimpleNamespace(
            pk=12,
            display_name="Title",
            artistid=SimpleNamespace(display_name="Artist"),
            yearid=SimpleNamespace(year=1966),
        )
        self.queryset.count.return_value = 1
        self.queryset.iterator.side_effect = lambda: iter([self.track])

    def test_selected_variants_use_script_text_and_skip_existing_files(self):
        self.path.side_effect = lambda track, num: SimpleNamespace(
            is_file=lambda: num == 2
        )
        result = generate_category_audio.run(5, [2, 3, 4], "missing")
        self.assertEqual(self.audio.call_args_list, [
            call("1966 music from Artist on K-V-W-J!", 12, 3, provider="openai", voice="nova"),
            call("That was Title from 1966 on J-95!", 12, 4, provider="openai", voice="nova"),
        ])
        self.assertEqual((result["generated"], result["skipped"], result["processed"]), (2, 1, 3))
        self.assertEqual(result["current_track"], "Title - Artist - 1966")

    def test_regeneration_overwrites_existing_selected_files(self):
        self.path.return_value.is_file.return_value = True
        result = generate_category_audio.run(5, [1, 2], "regenerate")
        self.assertEqual(self.audio.call_args_list, [
            call("Artist with Title on J-95!", 12, 1, provider="openai", voice="nova"),
            call("Title, that's Artist on K-V-W-J!", 12, 2, provider="openai", voice="nova"),
        ])
        self.assertEqual(result["generated"], 2)

    def test_missing_year_skips_only_year_variants(self):
        self.track.yearid = None
        result = generate_category_audio.run(5, [1, 3, 4], "regenerate")
        self.audio.assert_called_once_with(
            "Artist with Title on J-95!", 12, 1, provider="openai", voice="nova",
        )
        self.assertEqual((result["skipped"], result["processed"]), (2, 3))
        self.assertEqual(result["current_track"], "Title - Artist")

    def test_failure_is_reported_and_remaining_announcements_continue(self):
        self.audio.side_effect = [RuntimeError("Speech API unavailable"), None]
        with self.assertLogs("logviewer.tasks", level="ERROR"):
            result = generate_category_audio.run(5, [1, 2], "regenerate")
        self.assertEqual((result["generated"], result["failed"], result["processed"]), (1, 1, 2))
        self.assertIn("Speech API unavailable", result["errors"][0])

    def test_empty_category_completes_without_generation(self):
        self.queryset.count.return_value = 0
        self.queryset.iterator.side_effect = lambda: iter([])
        result = generate_category_audio.run(5, [1, 2], "missing")
        self.assertEqual(result["total"], 0)
        self.audio.assert_not_called()

    def test_form_requires_category_variants_and_valid_mode(self):
        form = CategoryAudioForm({"category": "", "variants": [], "mode": "bad"})
        self.assertFalse(form.is_valid())
        self.assertEqual(set(form.errors), {"category", "variants", "mode", "provider", "voice"})

    def test_selected_provider_and_voice_reach_audio_generation(self):
        for provider, voice in (("microsoft", "davis_dragon_hd"), ("openai", "ash")):
            with self.subTest(provider=provider):
                result = generate_category_audio.run(
                    5, [1], "regenerate", provider=provider, voice=voice,
                )
                self.audio.assert_called_once_with(
                    "Artist with Title on J-95!", 12, 1, provider=provider, voice=voice,
                )
                self.assertEqual(result["generated"], 1)
                self.audio.reset_mock()

    def test_dragonhd_preserves_titles_without_changing_artist_or_progress(self):
        self.track.display_name = "London Is Behind Me"
        self.track.artistid.display_name = "The Moody Blues"
        result = generate_category_audio.run(
            5, [1, 2, 3, 4], "regenerate", provider="microsoft", voice="nova_dragon_hd",
        )
        self.assertEqual(self.audio.call_args_list, [
            call("The Moody Blues with London Is Behind Me on J-95!", 12, 1, provider="microsoft", voice="nova_dragon_hd"),
            call("London Is Behind Me, that's The Moody Blues on K-V-W-J!", 12, 2, provider="microsoft", voice="nova_dragon_hd"),
            call("1966 music from The Moody Blues on K-V-W-J!", 12, 3, provider="microsoft", voice="nova_dragon_hd"),
            call("That was London Is Behind Me from 1966 on J-95!", 12, 4, provider="microsoft", voice="nova_dragon_hd"),
        ])
        self.assertEqual(result["current_track"], "London Is Behind Me - The Moody Blues - 1966")

    def test_queued_legacy_microsoft_category_job_keeps_voice_and_title_format(self):
        self.track.display_name = "London Is Behind Me"
        generate_category_audio.run(5, [1], "regenerate", provider="microsoft", voice="echo")
        self.audio.assert_called_once_with(
            "Artist with London is behind me on J-95!", 12, 1, provider="microsoft", voice="echo",
        )

    def test_male_category_variants_use_matching_text_and_skip_missing_year(self):
        self.track.yearid = None
        result = generate_category_audio.run(
            5, [5, 6, 7, 8], "regenerate", provider="microsoft", voice="andrew_dragon_hd",
        )
        self.assertEqual(self.audio.call_args_list, [
            call("Artist with Title on J-95!", 12, 5, provider="microsoft", voice="andrew_dragon_hd"),
            call("Title, that's Artist on K-V-W-J!", 12, 6, provider="microsoft", voice="andrew_dragon_hd"),
        ])
        self.assertEqual((result["generated"], result["skipped"]), (2, 2))

    def test_category_rejects_mixed_sets_and_mismatched_voice(self):
        for variants, voice, error in (
            ([1, 5], "nova", "variants"),
            ([5, 9], "ash", "variants"),
            ([9, 13], "ash", "variants"),
            ([5], "nova", "voice"),
            ([9], "nova", "voice"),
            ([1], "ash", "voice"),
        ):
            with self.subTest(variants=variants, voice=voice):
                form = CategoryAudioForm({
                    "category": "", "variants": variants, "mode": "missing",
                    "provider": "openai", "voice": voice,
                })
                self.assertIn(error, form.errors)

    def test_third_shift_category_generates_all_varieties_and_skips_missing_year(self):
        form = CategoryAudioForm({
            "category": "", "variants": [9, 10, 11, 12], "mode": "missing",
            "provider": "microsoft", "voice": "davis_dragon_hd",
        })
        self.assertNotIn("variants", form.errors)
        self.assertNotIn("voice", form.errors)
        result = generate_category_audio.run(
            5, [9, 10, 11, 12], "regenerate", provider="microsoft", voice="davis_dragon_hd",
        )
        self.assertEqual(self.audio.call_args_list, [
            call("Artist with Title on J-95!", 12, 9, provider="microsoft", voice="davis_dragon_hd"),
            call("Title, that's Artist on K-V-W-J!", 12, 10, provider="microsoft", voice="davis_dragon_hd"),
            call("1966 music from Artist on K-V-W-J!", 12, 11, provider="microsoft", voice="davis_dragon_hd"),
            call("That was Title from 1966 on J-95!", 12, 12, provider="microsoft", voice="davis_dragon_hd"),
        ])
        self.assertEqual(result["generated"], 4)
        self.track.yearid = None
        self.audio.reset_mock()
        result = generate_category_audio.run(
            5, [9, 10, 11, 12], "regenerate", provider="microsoft", voice="davis_dragon_hd",
        )
        self.assertEqual((result["generated"], result["skipped"]), (2, 2))
        self.assertEqual([entry.args[2] for entry in self.audio.call_args_list], [9, 10])

    def test_eighties_category_requires_emma_and_generates_four_varieties(self):
        data = {
            "category": "", "variants": [13, 14, 15, 16], "mode": "regenerate",
            "provider": "microsoft", "voice": "emma_dragon_hd",
        }
        form = CategoryAudioForm(data)
        self.assertNotIn("voice", form.errors)
        self.assertNotIn("variants", form.errors)
        for provider, voice in (("openai", "nova"), ("microsoft", "nova_dragon_hd")):
            form = CategoryAudioForm({**data, "provider": provider, "voice": voice})
            self.assertIn("Select Microsoft Emma DragonHD", str(form.errors["voice"]))
            with self.assertRaisesMessage(ValueError, "require Microsoft Emma DragonHD"):
                generate_category_audio.run(5, [13], "regenerate", provider=provider, voice=voice)
        result = generate_category_audio.run(
            5, [13, 14, 15, 16], "regenerate", provider="microsoft", voice="emma_dragon_hd",
        )
        self.assertEqual(self.audio.call_args_list, [
            call("Artist with Title on J-95!", 12, 13, provider="microsoft", voice="emma_dragon_hd"),
            call("Title, that's Artist on K-V-W-J!", 12, 14, provider="microsoft", voice="emma_dragon_hd"),
            call("1966 music from Artist on K-V-W-J!", 12, 15, provider="microsoft", voice="emma_dragon_hd"),
            call("That was Title from 1966 on J-95!", 12, 16, provider="microsoft", voice="emma_dragon_hd"),
        ])
        self.assertEqual(result["generated"], 4)
        self.track.yearid = None
        self.audio.reset_mock()
        result = generate_category_audio.run(
            5, [13, 14, 15, 16], "regenerate", provider="microsoft", voice="emma_dragon_hd",
        )
        self.assertEqual((result["generated"], result["skipped"]), (2, 2))

    def test_invalid_speech_options_stop_before_generation(self):
        for provider, voice in (("other", "nova"), ("microsoft", "ash"), ("openai", "echo")):
            with self.subTest(provider=provider, voice=voice):
                with self.assertRaises(ValueError):
                    generate_category_audio.run(
                        5, [1], "regenerate", provider=provider, voice=voice,
                    )
        self.audio.assert_not_called()

    def test_form_limits_voices_to_selected_provider(self):
        for provider, voice, valid in (
            ("microsoft", "nova_dragon_hd", True),
            ("microsoft", "ava_dragon_hd", True),
            ("microsoft", "emma_dragon_hd", True),
            ("microsoft", "andrew_dragon_hd", True),
            ("microsoft", "brian_dragon_hd", True),
            ("microsoft", "davis_dragon_hd", True),
            ("microsoft", "nova", False),
            ("microsoft", "echo", False),
            ("microsoft", "christopher", False),
            ("microsoft", "onyx", False),
            ("microsoft", "shimmer", False),
            ("openai", "nova", True),
            ("openai", "ash", True),
            ("microsoft", "ash", False),
            ("openai", "echo", False),
            ("openai", "onyx", False),
            ("openai", "shimmer", False),
            ("other", "nova", False),
        ):
            with self.subTest(provider=provider, voice=voice):
                form = CategoryAudioForm({
                    "category": "", "variants": [
                        5 if voice in ("ash", "andrew_dragon_hd", "brian_dragon_hd", "davis_dragon_hd") else 1,
                    ], "mode": "missing",
                    "provider": provider, "voice": voice,
                })
                self.assertEqual("voice" not in form.errors, valid)
                self.assertEqual("provider" in form.errors, provider == "other")
                if valid:
                    self.assertEqual(form.cleaned_data["voice"], voice)

    def test_voice_select_displays_gender_for_each_provider(self):
        for provider, labels in (
            ("openai", ("Nova (female)", "Ash (male)")),
            ("microsoft", (
                "Nova DragonHD (female)", "Ava DragonHD (female)", "Emma DragonHD (female)",
                "Andrew DragonHD (male)", "Brian DragonHD (male)", "Davis DragonHD (male)",
            )),
        ):
            with self.subTest(provider=provider):
                form = CategoryAudioForm({"provider": provider})
                options = str(form["voice"])
                for label in labels:
                    self.assertIn(label, options)

    @patch("logviewer.views.CategoryAudioForm")
    @patch("logviewer.views.generate_category_audio.delay")
    def test_submission_queues_selected_category_and_redirects(self, delay, form_class):
        task_id = "0b5940f9-5e4d-416a-bbd6-758c3dd84cc5"
        delay.return_value.id = task_id
        form_class.return_value.cleaned_data = {
            "category": SimpleNamespace(pk=5), "variants": [1, 4], "mode": "missing",
            "provider": "microsoft", "voice": "davis_dragon_hd",
        }
        response = category_audio(RequestFactory().post("/", {}))
        delay.assert_called_once_with(5, [1, 4], "missing", provider="microsoft", voice="davis_dragon_hd")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("category-audio-progress", args=[task_id]))

    @patch("logviewer.views.render")
    @patch("logviewer.views.CategoryAudioForm")
    @patch("logviewer.views.generate_category_audio.delay", side_effect=OperationalError("offline"))
    def test_broker_failure_is_visible(self, delay, form_class, render):
        form_class.return_value.cleaned_data = {
            "category": SimpleNamespace(pk=5), "variants": [1], "mode": "missing",
            "provider": "openai", "voice": "nova",
        }
        with self.assertLogs("logviewer.views", level="ERROR"):
            category_audio(RequestFactory().post("/", {}))
        self.assertEqual(render.call_args.kwargs["status"], 503)
        form_class.return_value.add_error.assert_called_once()


class SpeechAudioTests(SimpleTestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "42-1~0.mp3"
        path_patch = patch("logviewer.audio.announcement_path", return_value=self.path)
        path_patch.start()
        self.addCleanup(path_patch.stop)

    @patch("logviewer.audio.OpenAI")
    def test_openai_default_and_selected_voice_keep_dj_instructions(self, client):
        create = client.return_value.audio.speech.with_streaming_response.create
        for options, voice in (({}, "nova"), ({"voice": "ash"}, "ash")):
            with self.subTest(voice=voice):
                synthesize_audio("Artist with Title on J-95!", self.path, **options)
                create.assert_called_with(
                    model="gpt-4o-mini-tts", voice=voice,
                    input="Artist with Title on J-95!",
                    instructions="Speak with excitement like a radio DJ",
                )
                create.return_value.__enter__.return_value.stream_to_file.assert_called_with(self.path)

    @patch.dict(os.environ, {"AZURE_SPEECH_KEY": "test-key", "AZURE_SPEECH_REGION": "westus2"})
    @patch("logviewer.audio.requests.post")
    @patch("logviewer.audio.OpenAI")
    def test_microsoft_voices_send_escaped_ssml_and_write_mp3(self, client, post):
        post.return_value.content = b"mp3-audio"
        text = 'Artist & Title <live> with "quotes" and caf\u00e9'
        for voice, model in (
            ("nova_dragon_hd", "en-US-Nova:DragonHDLatestNeural"),
            ("ava_dragon_hd", "en-US-Ava:DragonHDLatestNeural"),
            ("emma_dragon_hd", "en-US-Emma:DragonHDLatestNeural"),
            ("andrew_dragon_hd", "en-US-Andrew:DragonHDLatestNeural"),
            ("brian_dragon_hd", "en-US-Brian:DragonHDLatestNeural"),
            ("davis_dragon_hd", "en-US-Davis:DragonHDLatestNeural"),
            ("nova", "en-US-NovaTurboMultilingualNeural"),
            ("echo", "en-US-EchoTurboMultilingualNeural"),
            ("christopher", "en-US-ChristopherNeural"),
            ("onyx", "en-US-OnyxTurboMultilingualNeural"),
            ("shimmer", "en-US-ShimmerTurboMultilingualNeural"),
        ):
            with self.subTest(voice=voice):
                synthesize_audio(text, self.path, provider="microsoft", voice=voice)
                args, kwargs = post.call_args
                self.assertEqual(args, ("https://westus2.tts.speech.microsoft.com/cognitiveservices/v1",))
                self.assertEqual(kwargs["headers"], {
                    "Ocp-Apim-Subscription-Key": "test-key",
                    "Content-Type": "application/ssml+xml",
                    "X-Microsoft-OutputFormat": "audio-48khz-192kbitrate-mono-mp3",
                })
                self.assertEqual(kwargs["timeout"], 60)
                root = ElementTree.fromstring(kwargs["data"])
                element = root.find("{http://www.w3.org/2001/10/synthesis}voice")
                self.assertEqual(element.attrib["name"], model)
                self.assertEqual(element.text, text)
                self.assertEqual(self.path.read_bytes(), b"mp3-audio")
        client.assert_not_called()

    @patch.dict(os.environ, {"AZURE_SPEECH_KEY": "test-key", "AZURE_SPEECH_REGION": "westus2"})
    @patch("logviewer.audio.requests.post")
    def test_microsoft_aliases_only_station_signoff(self, post):
        post.return_value.content = b"mp3-audio"
        text = "J-95 & Artist with <Title> on J-95!"
        synthesize_audio(text, self.path, provider="microsoft", voice="nova_dragon_hd")
        root = ElementTree.fromstring(post.call_args.kwargs["data"])
        voice = root.find("{http://www.w3.org/2001/10/synthesis}voice")
        self.assertEqual(voice.text, "J-95 & Artist with <Title> on ")
        alias = voice.find("{http://www.w3.org/2001/10/synthesis}sub")
        self.assertEqual(alias.attrib["alias"], "Jay ninety five")
        self.assertEqual(alias.text, "J-95")
        self.assertEqual(alias.tail, "!")

        synthesize_audio("J-95 Song on K-V-W-J!", self.path, provider="microsoft", voice="nova_dragon_hd")
        root = ElementTree.fromstring(post.call_args.kwargs["data"])
        voice = root.find("{http://www.w3.org/2001/10/synthesis}voice")
        self.assertEqual(voice.text, "J-95 Song on K-V-W-J!")
        self.assertIsNone(voice.find("{http://www.w3.org/2001/10/synthesis}sub"))

    @patch.dict(os.environ, {"AZURE_SPEECH_KEY": "test-key", "AZURE_SPEECH_REGION": "westus2"})
    @patch("logviewer.audio.requests.post")
    @patch("logviewer.audio.OpenAI")
    def test_microsoft_failures_preserve_existing_audio_without_fallback(self, client, post):
        self.path.write_bytes(b"existing-audio")
        post.return_value.raise_for_status.side_effect = requests.HTTPError("401 Unauthorized")
        with self.assertRaisesMessage(requests.HTTPError, "401 Unauthorized"):
            synthesize_audio("Text", self.path, provider="microsoft")
        self.assertEqual(self.path.read_bytes(), b"existing-audio")
        post.return_value.raise_for_status.side_effect = None
        post.return_value.content = b""
        with self.assertRaisesMessage(ValueError, "empty audio"):
            synthesize_audio("Text", self.path, provider="microsoft")
        self.assertEqual(self.path.read_bytes(), b"existing-audio")
        client.assert_not_called()

    @patch.dict(os.environ, {}, clear=True)
    @patch("logviewer.audio.requests.post")
    def test_missing_microsoft_key_is_explicit(self, post):
        with self.assertRaisesMessage(ValueError, "AZURE_SPEECH_KEY is required"):
            synthesize_audio("Text", self.path, provider="microsoft")
        post.assert_not_called()
        self.assertFalse(self.path.exists())

    @patch.dict(os.environ, {"AZURE_SPEECH_KEY": "test-key"}, clear=True)
    @patch("logviewer.audio.requests.post")
    def test_missing_microsoft_region_is_explicit(self, post):
        with self.assertRaisesMessage(ValueError, "AZURE_SPEECH_REGION is required"):
            synthesize_audio("Text", self.path, provider="microsoft", voice="nova_dragon_hd")
        post.assert_not_called()
        self.assertFalse(self.path.exists())


class AnnouncementVoiceTests(TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        path_patch = patch(
            "logviewer.audio.announcement_path",
            side_effect=lambda track, num: self.folder / f"{track}-{num}~0.mp3",
        )
        path_patch.start()
        self.addCleanup(path_patch.stop)

    @patch("logviewer.audio.synthesize_audio")
    def test_saved_audio_records_provider_and_updates_existing_metadata(self, synthesize):
        synthesize.side_effect = lambda text, path, provider, voice: path.write_bytes(b"audio")
        for provider, voice in (("openai", "nova"), ("microsoft", "nova_dragon_hd")):
            regenerate_audio("Text", 42, 1, provider=provider, voice=voice)
            metadata = AnnouncementVoice.objects.get(track_id=42, variant=1)
            self.assertEqual((metadata.provider, metadata.voice), (provider, voice))
            self.assertEqual((self.folder / "42-1~0.mp3").read_bytes(), b"audio")
        self.assertEqual(AnnouncementVoice.objects.count(), 1)

    @patch("logviewer.audio.synthesize_audio")
    def test_male_audio_keeps_independent_metadata_and_file(self, synthesize):
        synthesize.side_effect = lambda text, path, provider, voice: path.write_bytes(b"male")
        AnnouncementVoice.objects.create(track_id=42, variant=1, provider="openai", voice="nova")
        regenerate_audio("Text", 42, 5, provider="microsoft", voice="andrew_dragon_hd")
        self.assertEqual(
            list(AnnouncementVoice.objects.order_by("variant").values_list("variant", "provider", "voice")),
            [(1, "openai", "nova"), (5, "microsoft", "andrew_dragon_hd")],
        )
        self.assertEqual((self.folder / "42-5~0.mp3").read_bytes(), b"male")

    @patch("logviewer.audio.synthesize_audio", side_effect=ValueError("API error"))
    def test_failed_synthesis_preserves_audio_and_existing_metadata(self, synthesize):
        path = self.folder / "42-1~0.mp3"
        path.write_bytes(b"old")
        AnnouncementVoice.objects.create(track_id=42, variant=1, provider="openai", voice="nova")
        with self.assertRaisesMessage(ValueError, "API error"):
            regenerate_audio("Text", 42, 1, provider="microsoft", voice="echo")
        metadata = AnnouncementVoice.objects.get(track_id=42, variant=1)
        self.assertEqual((metadata.provider, metadata.voice), ("openai", "nova"))
        self.assertEqual(path.read_bytes(), b"old")
        self.assertEqual(list(self.folder.iterdir()), [path])

    @patch("logviewer.audio.synthesize_audio")
    def test_metadata_failure_after_replacement_leaves_unknown_voice(self, synthesize):
        synthesize.side_effect = lambda text, path, provider, voice: path.write_bytes(b"new")
        AnnouncementVoice.objects.create(track_id=42, variant=1, provider="openai", voice="nova")
        original_save = AnnouncementVoice.save

        def save(instance, *args, **kwargs):
            if instance.provider:
                raise DatabaseError("database unavailable")
            return original_save(instance, *args, **kwargs)

        with patch.object(AnnouncementVoice, "save", autospec=True, side_effect=save):
            with self.assertRaisesMessage(DatabaseError, "database unavailable"):
                regenerate_audio("Text", 42, 1, provider="microsoft", voice="echo")
        metadata = AnnouncementVoice.objects.get(track_id=42, variant=1)
        self.assertEqual((metadata.provider, metadata.voice), ("", ""))
        self.assertEqual((self.folder / "42-1~0.mp3").read_bytes(), b"new")

    @patch("logviewer.management.commands.backfill_announcement_voices.Track.objects")
    @patch("logviewer.management.commands.backfill_announcement_voices.announcement_path")
    def test_backfill_only_existing_files_and_never_overwrites_records(self, path, tracks):
        tracks.values_list.return_value.iterator.return_value = iter([42])
        path.side_effect = lambda track, num: self.folder / f"{track}-{num}~0.mp3"
        (self.folder / "42-1~0.mp3").write_bytes(b"one")
        (self.folder / "42-2~0.mp3").write_bytes(b"two")
        AnnouncementVoice.objects.create(track_id=42, variant=2, provider="microsoft", voice="echo")
        output = StringIO()
        call_command("backfill_announcement_voices", confirm_openai_nova=True, stdout=output)
        self.assertEqual(
            list(AnnouncementVoice.objects.order_by("variant").values_list("variant", "provider", "voice")),
            [(1, "openai", "nova"), (2, "microsoft", "echo")],
        )
        self.assertIn("Recorded 1", output.getvalue())

    def test_backfill_requires_explicit_legacy_assumption(self):
        with self.assertRaisesMessage(CommandError, "Pass --confirm-openai-nova"):
            call_command("backfill_announcement_voices")


class VoicePreviewTests(SimpleTestCase):
    @patch("logviewer.tasks.synthesize_audio")
    @patch("logviewer.tasks.Track.objects")
    def test_preview_uses_selected_random_track_and_voice_without_saving_announcement(self, tracks, synthesize):
        query = tracks.select_related.return_value.filter.return_value.order_by.return_value
        query.first.return_value = SimpleNamespace(
            display_name="London Is Behind Me",
            artistid=SimpleNamespace(display_name="The Moody Blues"),
        )
        synthesize.side_effect = lambda text, path, provider, voice: path.write_bytes(b"preview")
        for voice in (
            "nova_dragon_hd", "ava_dragon_hd", "emma_dragon_hd",
            "andrew_dragon_hd", "brian_dragon_hd", "davis_dragon_hd",
        ):
            with self.subTest(voice=voice):
                result = generate_voice_preview.run("microsoft", voice)
                tracks.select_related.return_value.filter.return_value.order_by.assert_called_with("?")
                args = synthesize.call_args.args
                self.assertEqual(args[0], "The Moody Blues with London Is Behind Me on J-95!")
                self.assertEqual(args[2:], ("microsoft", voice))
                self.assertFalse(args[1].exists())
                self.assertEqual(base64.b64decode(result["audio"]), b"preview")
                self.assertEqual((result["track_title"], result["track_artist"]), ("London Is Behind Me", "The Moody Blues"))
        generate_voice_preview.run("openai", "nova")
        self.assertEqual(
            synthesize.call_args.args[0],
            "The Moody Blues with London Is Behind Me on J-95!",
        )
        generate_voice_preview.run("microsoft", "echo")
        self.assertEqual(
            synthesize.call_args.args[0],
            "The Moody Blues with London is behind me on J-95!",
        )

    @patch("logviewer.tasks.synthesize_audio")
    @patch("logviewer.tasks.Track.objects")
    def test_each_preview_selects_a_track_and_reports_its_original_names(self, tracks, synthesize):
        query = tracks.select_related.return_value.filter.return_value.order_by.return_value
        query.first.side_effect = [
            SimpleNamespace(display_name="First Song", artistid=SimpleNamespace(display_name="First Artist")),
            SimpleNamespace(display_name="Second Song", artistid=SimpleNamespace(display_name="Second Artist")),
        ]
        synthesize.side_effect = lambda text, path, provider, voice: path.write_bytes(b"preview")
        results = [generate_voice_preview.run("openai", "nova") for _ in range(2)]
        self.assertEqual(
            [(result["track_title"], result["track_artist"]) for result in results],
            [("First Song", "First Artist"), ("Second Song", "Second Artist")],
        )
        self.assertEqual(query.first.call_count, 2)

    @patch("logviewer.tasks.synthesize_audio")
    @patch("logviewer.tasks.Track.objects")
    def test_preview_without_eligible_tracks_fails_before_synthesis(self, tracks, synthesize):
        tracks.select_related.return_value.filter.return_value.order_by.return_value.first.return_value = None
        with self.assertRaisesMessage(ValueError, "No tracks with a title and artist"):
            generate_voice_preview.run("openai", "nova")
        synthesize.assert_not_called()

    @patch("logviewer.tasks.synthesize_audio")
    @patch("logviewer.tasks.preview_tracks")
    def test_preview_uses_selected_track_and_supports_comparing_voices(self, tracks, synthesize):
        track = SimpleNamespace(
            display_name="Chosen Song", artistid=SimpleNamespace(display_name="Chosen Artist"),
        )
        tracks.return_value.filter.return_value.first.return_value = track
        synthesize.side_effect = lambda text, path, provider, voice: path.write_bytes(b"preview")
        for voice in ("nova_dragon_hd", "andrew_dragon_hd"):
            result = generate_voice_preview.run("microsoft", voice, track_id=42)
            self.assertEqual((result["track_title"], result["track_artist"]), ("Chosen Song", "Chosen Artist"))
            synthesize.assert_called_with(
                "Chosen Artist with Chosen Song on J-95!", synthesize.call_args.args[1], "microsoft", voice,
            )
        self.assertEqual(tracks.return_value.filter.call_args_list, [call(pk=42), call(pk=42)])
        tracks.return_value.order_by.assert_not_called()

    @patch("logviewer.tasks.synthesize_audio")
    @patch("logviewer.tasks.preview_tracks")
    def test_deleted_selected_track_fails_before_synthesis(self, tracks, synthesize):
        tracks.return_value.filter.return_value.first.return_value = None
        with self.assertRaisesMessage(ValueError, "selected preview track is no longer available"):
            generate_voice_preview.run("openai", "nova", track_id=42)
        synthesize.assert_not_called()

    @patch("logviewer.views.preview_tracks")
    def test_random_track_endpoint_returns_display_names_and_excludes_current(self, tracks):
        query = tracks.return_value
        track = SimpleNamespace(pk=42, display_name="Chosen Song", artistid=SimpleNamespace(display_name="Artist"))
        query.order_by.return_value.first.return_value = track
        response = random_preview_track(RequestFactory().get("/"))
        self.assertEqual(json.loads(response.content), {
            "track_id": "42", "track_title": "Chosen Song", "track_artist": "Artist",
        })
        query.order_by.assert_called_once_with("?")
        query.exclude.return_value.order_by.return_value.first.return_value = None
        response = random_preview_track(RequestFactory().get("/", {"exclude": "42"}))
        query.exclude.assert_called_once_with(pk=42)
        self.assertEqual(response.status_code, 404)
        self.assertIn("No other tracks", json.loads(response.content)["error"])

    @patch("logviewer.views.preview_tracks")
    def test_random_track_endpoint_reports_invalid_id_and_empty_catalog(self, tracks):
        response = random_preview_track(RequestFactory().get("/", {"exclude": "-1"}))
        self.assertEqual(response.status_code, 400)
        tracks.return_value.order_by.return_value.first.return_value = None
        response = random_preview_track(RequestFactory().get("/"))
        self.assertEqual(response.status_code, 404)
        self.assertIn("No tracks", json.loads(response.content)["error"])

    @patch("logviewer.views.generate_voice_preview.delay")
    def test_preview_endpoint_validates_and_queues_selected_voice(self, delay):
        delay.return_value.id = "task-id"
        response = voice_preview(RequestFactory().post("/", {"provider": "microsoft", "voice": "nova_dragon_hd"}))
        self.assertEqual(json.loads(response.content), {"task_id": "task-id"})
        delay.assert_called_once_with("microsoft", "nova_dragon_hd")
        delay.reset_mock()
        response = voice_preview(RequestFactory().post("/", {"provider": "openai", "voice": "echo"}))
        self.assertEqual(response.status_code, 400)
        response = voice_preview(RequestFactory().post("/", {"provider": "microsoft", "voice": "echo"}))
        self.assertEqual(response.status_code, 400)
        delay.assert_not_called()

    @patch("logviewer.views.generate_voice_preview.delay")
    def test_preview_endpoint_queues_exact_track_and_rejects_invalid_ids(self, delay):
        delay.return_value.id = "task-id"
        data = {"provider": "microsoft", "voice": "nova_dragon_hd", "track_id": "42"}
        response = voice_preview(RequestFactory().post("/", data))
        self.assertEqual(response.status_code, 200)
        delay.assert_called_once_with("microsoft", "nova_dragon_hd", track_id=42)
        delay.reset_mock()
        for invalid in ("", "0", "-1", "xyz", "9" * 50):
            with self.subTest(track_id=invalid):
                response = voice_preview(RequestFactory().post("/", {**data, "track_id": invalid}))
                self.assertEqual(response.status_code, 400)
        delay.assert_not_called()

    @patch("logviewer.views.generate_voice_preview.delay", side_effect=OperationalError("offline"))
    def test_preview_broker_failure_is_visible(self, delay):
        with self.assertLogs("logviewer.views", level="ERROR"):
            response = voice_preview(
                RequestFactory().post("/", {"provider": "microsoft", "voice": "nova_dragon_hd"}),
            )
        self.assertEqual(response.status_code, 503)
        self.assertIn("Could not start preview", json.loads(response.content)["error"])


class AnnouncementTableTests(SimpleTestCase):
    def test_track_and_playback_pickers_only_offer_eighties_for_matching_categories(self):
        for category, include_eighties in (
            ("80s Rock", True), ("THE 80S SHOW", True),
            ("Rock", False), (None, False),
        ):
            track = Track(pk=42, categoryid=Category(pk=1, category=category))
            for table_class, record in (
                (TracksTable, track),
                (PlaybackTable, Playback(pk=100, trackid=track)),
            ):
                with self.subTest(category=category, table=table_class.__name__):
                    row = next(iter(table_class([record]).rows))
                    picker = str(row.get_cell("announcements"))
                    for num in range(1, 13):
                        self.assertIn(f"/media/42-{num}~0.mp3", picker)
                    for label in ("Morning (Nova)", "Afternoon (Andrew)", "Night (Davis)"):
                        self.assertIn(f'label="{label}"', picker)
                        self.assertIn(f"{label} 1</option>", picker)
                    for num in range(13, 17):
                        self.assertEqual(f"/media/42-{num}~0.mp3" in picker, include_eighties)
                    self.assertEqual('label="80s show (Emma)"' in picker, include_eighties)

    def test_new_playback_controls_are_excluded_from_data_exports(self):
        for table_class in (TracksTable, PlaybackTable):
            with self.subTest(table=table_class.__name__):
                headers = next(table_class([]).as_values())
                self.assertNotIn("Announcements", headers)


class ArtistAnnouncementChoicesTests(SimpleTestCase):
    def test_form_only_accepts_eighties_when_artist_is_eligible(self):
        for include_eighties in (False, True):
            with self.subTest(include_eighties=include_eighties):
                form = ArtistAudioForm(
                    {"voice_set": "eighties", "variants": [1], "mode": "missing"},
                    include_eighties=include_eighties,
                )
                self.assertEqual(form.is_valid(), include_eighties)
                self.assertEqual(
                    list(form.fields["voice_set"].choices),
                    [
                        ("female", "Morning (Nova)"), ("male", "Afternoon (Andrew)"),
                        ("male_2", "Night (Davis)"),
                    ] + ([("eighties", "80s show (Emma)")] if include_eighties else []),
                )

    @patch("logviewer.views.render")
    @patch("logviewer.views.ArtistDisplayForm")
    @patch("logviewer.views.TracksTable")
    @patch("logviewer.views.get_object_or_404")
    def test_artist_page_checks_for_any_eighties_track(self, get_artist, table, display_form, render):
        from .views import artist_edit

        tracks = get_artist.return_value.track_set.select_related.return_value
        for eligible in (False, True):
            with self.subTest(eligible=eligible):
                tracks.filter.return_value.exists.return_value = eligible
                artist_edit(RequestFactory().get("/"), 7)
                tracks.filter.assert_called_with(categoryid__category__icontains="80s")
                choices = dict(render.call_args.args[2]["audio_form"].fields["voice_set"].choices)
                self.assertEqual("eighties" in choices, eligible)


class TrackAnnouncementStatusTests(SimpleTestCase):
    def setUp(self):
        settings_patch = patch("logviewer.views.UtilitySettings.objects.filter")
        self.settings_lookup = settings_patch.start()
        self.settings_lookup.return_value.first.return_value = None
        self.addCleanup(settings_patch.stop)
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.path_patch = patch(
            "logviewer.views.announcement_path",
            side_effect=lambda track, num: self.folder / f"{track}-{num}~0.mp3",
        )
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)
        self.records_patch = patch("logviewer.views.AnnouncementVoice.objects.filter")
        records = self.records_patch.start()
        records.return_value = [
            SimpleNamespace(variant=1, provider="microsoft", voice="echo"),
            SimpleNamespace(variant=2, provider="openai", voice="nova"),
            SimpleNamespace(variant=4, provider="", voice=""),
        ]
        self.addCleanup(self.records_patch.stop)
        for variant in (1, 3, 4):
            (self.folder / f"42-{variant}~0.mp3").write_bytes(b"audio")
        self.track = Track(
            pk=42, track_name="Title", artistid=Artist(pk=7, artist_name="Artist"),
            categoryid=Category(pk=1, category="80s Rock"),
            yearid_id=1966,
        )
        self.track_patch = patch("logviewer.views.get_object_or_404", return_value=self.track)
        self.track_patch.start()
        self.addCleanup(self.track_patch.stop)

    def test_edit_page_shows_file_status_and_recorded_voice_for_each_variant(self):
        from .views import track_and_artist_edit

        response = track_and_artist_edit(RequestFactory().get("/"), 42)
        self.assertContains(response, "File exists: Microsoft Speech Service / Echo Turbo (male)")
        self.assertContains(
            response, '<span class="audio-file-info text-muted">File missing</span>', count=13,
        )
        self.assertContains(response, "File exists: service and voice unknown", count=2)
        self.assertContains(response, "Regenerate", count=16)
        for num in range(1, 17):
            self.assertContains(response, f'/media/42-{num}~0.mp3')
        for label in (
            "Morning (Nova DragonHD)", "Afternoon (Andrew DragonHD)",
            "Night (Davis DragonHD)", "80s show (Emma DragonHD)",
        ):
            self.assertContains(response, label, count=4)

    def test_shift_labels_use_assigned_voices_not_recorded_file_metadata(self):
        from .views import track_and_artist_edit, track_announcement_status

        self.settings_lookup.return_value.first.return_value = UtilitySettings(
            track_speech_provider="openai", track_speech_voice="nova",
            male_track_speech_provider="openai", male_track_speech_voice="ash",
            second_male_track_speech_provider="microsoft", second_male_track_speech_voice="brian_dragon_hd",
        )
        expected = ("Morning (Nova)", "Afternoon (Ash)", "Night (Brian DragonHD)", "80s show (Emma DragonHD)")
        response = track_and_artist_edit(RequestFactory().get("/"), 42)
        for label in expected:
            self.assertContains(response, label, count=4)
        self.assertContains(response, "File exists: Microsoft Speech Service / Echo Turbo (male)")
        response = track_announcement_status(RequestFactory().get("/"), 42)
        statuses = json.loads(response.content)["announcements"]
        self.assertEqual([row["shift"] for row in statuses], [label for label in expected for _ in range(4)])

    def test_status_endpoint_reports_current_files_and_ignores_stale_metadata(self):
        from .views import track_announcement_status

        response = track_announcement_status(RequestFactory().get("/"), 42)
        statuses = json.loads(response.content)["announcements"]
        self.assertEqual(
            [(item["number"], item["display"]) for item in statuses],
            [
                (1, "File exists: Microsoft Speech Service / Echo Turbo (male)"),
                (2, "File missing"),
                (3, "File exists: service and voice unknown"),
                (4, "File exists: service and voice unknown"),
                (5, "File missing"),
                (6, "File missing"),
                (7, "File missing"),
                (8, "File missing"),
                (9, "File missing"),
                (10, "File missing"),
                (11, "File missing"),
                (12, "File missing"),
                (13, "File missing"),
                (14, "File missing"),
                (15, "File missing"),
                (16, "File missing"),
            ],
        )
        (self.folder / "42-1~0.mp3").unlink()
        response = track_announcement_status(RequestFactory().get("/"), 42)
        self.assertEqual(json.loads(response.content)["announcements"][0]["display"], "File missing")

    def test_status_distinguishes_legacy_and_dragonhd_voices(self):
        from .views import announcement_file_statuses

        with patch("logviewer.views.AnnouncementVoice.objects.filter") as records:
            for voice, label in (
                ("nova", "Nova Turbo (female)"),
                ("shimmer", "Shimmer Turbo (female)"),
                ("echo", "Echo Turbo (male)"),
                ("christopher", "Christopher standard (male)"),
                ("onyx", "Onyx Turbo (male)"),
                ("nova_dragon_hd", "Nova DragonHD (female)"),
            ):
                with self.subTest(voice=voice):
                    records.return_value = [SimpleNamespace(variant=1, provider="microsoft", voice=voice)]
                    self.assertEqual(
                        announcement_file_statuses(self.track)[0]["display"],
                        f"File exists: Microsoft Speech Service / {label}",
                    )

    def test_missing_year_still_shows_status_for_all_variants(self):
        from .views import track_and_artist_edit

        self.track.yearid_id = None
        response = track_and_artist_edit(RequestFactory().get("/"), 42)
        self.assertContains(response, "Regenerate", count=8)
        self.assertContains(response, "Regeneration requires a track year.", count=8)
        self.assertContains(response, "File exists: service and voice unknown", count=2)

    def test_unavailable_directory_is_not_reported_as_missing(self):
        from .views import track_announcement_status

        self.path_patch.stop()
        with patch(
            "logviewer.views.announcement_path",
            side_effect=lambda track, num: self.folder / "unmounted" / f"{track}-{num}~0.mp3",
        ), self.assertLogs("logviewer.views", level="WARNING"):
            response = track_announcement_status(RequestFactory().get("/"), 42)
        statuses = json.loads(response.content)["announcements"]
        self.assertEqual(
            [item["display"] for item in statuses],
            ["File status unavailable"] * 16,
        )

    def test_eighties_announcements_only_appear_for_eighties_categories(self):
        from .views import track_and_artist_edit, track_announcement_status

        for category, include_eighties in (
            (Category(pk=1, category="Classic 80S Rock"), True),
            (Category(pk=2, category="Regular rotation"), False),
            (None, False),
        ):
            with self.subTest(category=category):
                self.track.categoryid = category
                response = track_and_artist_edit(RequestFactory().get("/"), 42)
                if include_eighties:
                    self.assertContains(response, "80s show (Emma DragonHD)", count=4)
                else:
                    self.assertNotContains(response, "80s show")
                    for num in range(13, 17):
                        self.assertNotContains(response, f"/media/42-{num}~0.mp3")
                self.assertContains(response, "Regenerate", count=16 if include_eighties else 12)
                response = track_announcement_status(RequestFactory().get("/"), 42)
                statuses = json.loads(response.content)["announcements"]
                self.assertEqual(
                    [row["number"] for row in statuses],
                    list(range(1, 17 if include_eighties else 13)),
                )


class ArtistGenerationModeTests(SimpleTestCase):
    @patch.object(regenerate_artist_audio, "update_state")
    @patch("logviewer.tasks.Artist.objects.get")
    @patch("logviewer.tasks.Track.objects")
    def test_modes_preserve_or_replace_existing_files_for_each_set(self, tracks, get_artist, update):
        get_artist.return_value = SimpleNamespace(display_name="Artist")
        tracks.filter.return_value.select_related.return_value = [
            SimpleNamespace(pk=42, display_name="Title", yearid=SimpleNamespace(year=1985)),
            SimpleNamespace(pk=43, display_name="No year", yearid=None),
        ]
        for offset, voice in (
            (0, "nova_dragon_hd"), (4, "andrew_dragon_hd"),
            (8, "davis_dragon_hd"), (12, "emma_dragon_hd"),
        ):
            for mode in ("missing", "regenerate"):
                with self.subTest(offset=offset, mode=mode), TemporaryDirectory() as folder:
                    def path(track_id, num):
                        return Path(folder) / f"{track_id}-{num}~0.mp3"

                    existing = path(42, offset + 1)
                    existing.write_bytes(b"original")
                    unselected = path(42, offset + 2)
                    unselected.write_bytes(b"unselected")
                    with patch("logviewer.tasks.announcement_path", side_effect=path), patch(
                        "logviewer.tasks.regenerate_audio",
                        side_effect=lambda text, track_id, num, **kwargs: path(track_id, num).write_bytes(b"new"),
                    ) as generate:
                        result = regenerate_artist_audio.run(
                            7, variants=[offset + 1, offset + 3],
                            provider="microsoft", voice=voice, mode=mode,
                        )
                    expected = [(42, offset + 3), (43, offset + 1)]
                    if mode == "regenerate":
                        expected.insert(0, (42, offset + 1))
                    self.assertEqual(
                        [(item.args[1], item.args[2]) for item in generate.call_args_list], expected,
                    )
                    self.assertEqual(existing.read_bytes(), b"original" if mode == "missing" else b"new")
                    self.assertEqual(unselected.read_bytes(), b"unselected")
                    self.assertEqual(path(42, offset + 3).read_bytes(), b"new")
                    self.assertEqual(path(43, offset + 1).read_bytes(), b"new")
                    self.assertFalse(path(43, offset + 3).exists())
                    self.assertEqual(
                        (result["total"], result["completed"], result["skipped"]),
                        (4, 2, 2) if mode == "missing" else (4, 3, 1),
                    )
                    self.assertEqual(result["tracks"]["42"]["skipped"], 1 if mode == "missing" else 0)
                    self.assertEqual(result["tracks"]["43"]["skipped"], 1)
                    self.assertTrue(all(track["status"] == "complete" for track in result["tracks"].values()))

    @patch("logviewer.tasks.Artist.objects.get")
    def test_invalid_mode_is_rejected_before_loading_tracks(self, get_artist):
        with self.assertRaisesMessage(ValueError, "Unsupported generation mode"):
            regenerate_artist_audio.run(7, mode="invalid")
        get_artist.assert_not_called()


class SingleAnnouncementAudioTests(SimpleTestCase):
    def setUp(self):
        self.track = SimpleNamespace(
            pk=42,
            display_name="Title",
            artistid=SimpleNamespace(display_name="Artist"),
            yearid=SimpleNamespace(year=1966),
        )
        self.track_query = patch("logviewer.tasks.Track.objects")
        tracks = self.track_query.start()
        self.addCleanup(self.track_query.stop)
        tracks.select_related.return_value.get.return_value = self.track
        self.update_state = patch.object(regenerate_single_audio, "update_state")
        self.update_state.start()
        self.addCleanup(self.update_state.stop)
        self.generate = patch("logviewer.tasks.regenerate_audio")
        self.generate_audio = self.generate.start()
        self.addCleanup(self.generate.stop)
        settings_patch = patch(
            "logviewer.views.UtilitySettings.objects.get_or_create",
            return_value=(SimpleNamespace(track_speech_provider="openai", track_speech_voice="nova"), False),
        )
        settings_patch.start()
        self.addCleanup(settings_patch.stop)

    def test_year_announcement_variants_use_their_expected_text(self):
        for num, expected_text in (
            (3, "1966 music from Artist on K-V-W-J!"),
            (4, "That was Title from 1966 on J-95!"),
        ):
            with self.subTest(announcement=num):
                result = regenerate_single_audio.run(42, num)
                self.assertEqual(result["announcement"], num)
                self.assertEqual(
                    self.generate_audio.call_args,
                    call(expected_text, 42, num, provider="openai", voice="nova"),
                )
                self.generate_audio.reset_mock()

    def test_year_announcement_rejects_track_without_year(self):
        self.track.yearid = None

        with self.assertRaisesMessage(ValueError, "requires a year"):
            regenerate_single_audio.run(42, 3)

        self.generate_audio.assert_not_called()

    def test_male_variants_use_same_text_and_year_rules(self):
        for num, text in (
            (5, "Artist with Title on J-95!"),
            (6, "Title, that's Artist on K-V-W-J!"),
            (7, "1966 music from Artist on K-V-W-J!"),
            (8, "That was Title from 1966 on J-95!"),
        ):
            with self.subTest(num=num):
                regenerate_single_audio.run(42, num, provider="microsoft", voice="andrew_dragon_hd")
                self.generate_audio.assert_called_with(text, 42, num, provider="microsoft", voice="andrew_dragon_hd")
        self.track.yearid = None
        with self.assertRaisesMessage(ValueError, "requires a year"):
            regenerate_single_audio.run(42, 8, provider="microsoft", voice="andrew_dragon_hd")

    def test_third_shift_variants_use_same_text_and_year_rules(self):
        for num, text in (
            (9, "Artist with Title on J-95!"),
            (10, "Title, that's Artist on K-V-W-J!"),
            (11, "1966 music from Artist on K-V-W-J!"),
            (12, "That was Title from 1966 on J-95!"),
        ):
            with self.subTest(num=num):
                regenerate_single_audio.run(42, num, provider="microsoft", voice="davis_dragon_hd")
                self.generate_audio.assert_called_with(text, 42, num, provider="microsoft", voice="davis_dragon_hd")
        self.track.yearid = None
        for num in (11, 12):
            with self.assertRaisesMessage(ValueError, "requires a year"):
                regenerate_single_audio.run(42, num, provider="microsoft", voice="davis_dragon_hd")

    def test_eighties_single_and_artist_tasks_require_emma(self):
        with self.assertRaisesMessage(ValueError, "require Microsoft Emma DragonHD"):
            regenerate_single_audio.run(42, 13, provider="microsoft", voice="nova_dragon_hd")
        with self.assertRaisesMessage(ValueError, "require Microsoft Emma DragonHD"):
            regenerate_artist_audio.run(7, variants=[13], provider="openai", voice="nova")
        for num in (13, 14, 15, 16):
            result = regenerate_single_audio.run(42, num, provider="microsoft", voice="emma_dragon_hd")
            self.assertEqual(result["announcement"], num)
        self.track.yearid = None
        for num in (15, 16):
            with self.assertRaisesMessage(ValueError, "requires a year"):
                regenerate_single_audio.run(42, num, provider="microsoft", voice="emma_dragon_hd")

    def test_task_uses_selected_service_and_voice(self):
        self.track.display_name = "London Is Behind Me"
        self.track.artistid.display_name = "The Moody Blues"
        result = regenerate_single_audio.run(42, 1, provider="microsoft", voice="brian_dragon_hd")
        self.generate_audio.assert_called_once_with(
            "The Moody Blues with London Is Behind Me on J-95!",
            42, 1, provider="microsoft", voice="brian_dragon_hd",
        )
        self.assertEqual(result["track_name"], "London Is Behind Me")

    def test_queued_legacy_microsoft_track_job_keeps_voice_and_title_format(self):
        self.track.display_name = "London Is Behind Me"
        regenerate_single_audio.run(42, 1, provider="microsoft", voice="onyx")
        self.generate_audio.assert_called_once_with(
            "Artist with London is behind me on J-95!", 42, 1, provider="microsoft", voice="onyx",
        )

    @patch("logviewer.tasks.regenerate_audio")
    @patch("logviewer.tasks.Track.objects")
    @patch("logviewer.tasks.Artist.objects.get")
    @patch.object(regenerate_artist_audio, "update_state")
    def test_artist_wide_generation_keeps_original_title(self, update, get_artist, tracks, generate):
        artist = SimpleNamespace(display_name="The Moody Blues")
        get_artist.return_value = artist
        tracks.filter.return_value = SimpleNamespace(
            select_related=lambda *args: [
                SimpleNamespace(pk=42, display_name="London Is Behind Me", yearid=None),
            ],
        )
        result = regenerate_artist_audio.run(7)
        self.assertEqual(result["completed"], 2)
        self.assertEqual(generate.call_args_list, [
            call("The Moody Blues with London Is Behind Me on J-95!", 42, 1, provider="openai", voice="nova"),
            call("London Is Behind Me, that's The Moody Blues on K-V-W-J!", 42, 2, provider="openai", voice="nova"),
        ])

    @patch("logviewer.tasks.regenerate_audio")
    @patch("logviewer.tasks.Track.objects")
    @patch("logviewer.tasks.Artist.objects.get")
    @patch.object(regenerate_artist_audio, "update_state")
    def test_artist_male_generation_and_missing_year(self, update, get_artist, tracks, generate):
        get_artist.return_value = SimpleNamespace(display_name="Artist")
        tracks.filter.return_value.select_related.return_value = [
            SimpleNamespace(pk=42, display_name="Title", yearid=None),
        ]
        result = regenerate_artist_audio.run(
            7, variants=[5, 7, 8], provider="microsoft", voice="andrew_dragon_hd",
        )
        generate.assert_called_once_with(
            "Artist with Title on J-95!", 42, 5, provider="microsoft", voice="andrew_dragon_hd",
        )
        self.assertEqual((result["completed"], result["skipped"]), (1, 2))

    @patch("logviewer.tasks.regenerate_audio")
    @patch("logviewer.tasks.Track.objects")
    @patch("logviewer.tasks.Artist.objects.get")
    @patch.object(regenerate_artist_audio, "update_state")
    def test_artist_third_shift_generation_and_missing_year(self, update, get_artist, tracks, generate):
        get_artist.return_value = SimpleNamespace(display_name="Artist")
        tracks.filter.return_value.select_related.return_value = [
            SimpleNamespace(pk=42, display_name="Title", yearid=None),
        ]
        result = regenerate_artist_audio.run(
            7, variants=[9, 10, 11, 12], provider="microsoft", voice="davis_dragon_hd",
        )
        self.assertEqual([entry.args[2] for entry in generate.call_args_list], [9, 10])
        self.assertEqual((result["completed"], result["skipped"]), (2, 2))

    @patch("logviewer.tasks.regenerate_audio")
    @patch("logviewer.tasks.Track.objects")
    @patch("logviewer.tasks.Artist.objects.get")
    @patch.object(regenerate_artist_audio, "update_state")
    def test_artist_eighties_generation_skips_missing_year(self, update, get_artist, tracks, generate):
        get_artist.return_value = SimpleNamespace(display_name="Artist")
        tracks.filter.return_value.select_related.return_value = [
            SimpleNamespace(pk=42, display_name="Title", yearid=None),
        ]
        result = regenerate_artist_audio.run(
            7, variants=[13, 14, 15, 16], provider="microsoft", voice="emma_dragon_hd",
        )
        self.assertEqual([entry.args[2] for entry in generate.call_args_list], [13, 14])
        self.assertEqual((result["completed"], result["skipped"]), (2, 2))

    @patch("logviewer.views.get_object_or_404")
    @patch("logviewer.views.regenerate_single_audio.delay")
    def test_start_endpoint_queues_year_variant(self, delay, get_track):
        from .views import start_single_audio

        get_track.return_value = SimpleNamespace(pk=42, yearid_id=1966)
        delay.return_value.id = "task-id"
        response = start_single_audio(RequestFactory().post("/"), 42, 4)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content), {"task_id": "task-id"})
        delay.assert_called_once_with(42, 4, provider="openai", voice="nova")

    @patch("logviewer.views.get_object_or_404")
    @patch("logviewer.views.regenerate_single_audio.delay")
    def test_start_endpoint_rejects_year_variant_without_year(self, delay, get_track):
        from .views import start_single_audio

        get_track.return_value = SimpleNamespace(pk=42, yearid_id=None)
        for num in (3, 4, 7, 8, 11, 12, 15, 16):
            with self.subTest(num=num):
                response = start_single_audio(RequestFactory().post("/"), 42, num)
                self.assertEqual(response.status_code, 400)
                self.assertIn("requires a track year", json.loads(response.content)["error"])
        delay.assert_not_called()
