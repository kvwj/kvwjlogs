import json
from django.test import TestCase
from django.urls import reverse
from django.test import SimpleTestCase
from types import SimpleNamespace
from unittest.mock import patch, call
from kombu.exceptions import OperationalError

from .models import RdsDisplayState
from .tasks import generate_category_audio, regenerate_single_audio
from .forms import CategoryAudioForm
from .views import category_audio
from django.test import RequestFactory
from .models import Track, Playback
from .tables import TracksTable, PlaybackTable


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
            call("1966 music from Artist on K-V-W-J!", 12, 3),
            call("That was Title from 1966 on J-95!", 12, 4),
        ])
        self.assertEqual((result["generated"], result["skipped"], result["processed"]), (2, 1, 3))
        self.assertEqual(result["current_track"], "Title - Artist - 1966")

    def test_regeneration_overwrites_existing_selected_files(self):
        self.path.return_value.is_file.return_value = True
        result = generate_category_audio.run(5, [1, 2], "regenerate")
        self.assertEqual(self.audio.call_args_list, [
            call("Artist with Title on J-95!", 12, 1),
            call("Title, that's Artist on K-V-W-J!", 12, 2),
        ])
        self.assertEqual(result["generated"], 2)

    def test_missing_year_skips_only_year_variants(self):
        self.track.yearid = None
        result = generate_category_audio.run(5, [1, 3, 4], "regenerate")
        self.audio.assert_called_once_with("Artist with Title on J-95!", 12, 1)
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
        self.assertEqual(set(form.errors), {"category", "variants", "mode"})

    @patch("logviewer.views.CategoryAudioForm")
    @patch("logviewer.views.generate_category_audio.delay")
    def test_submission_queues_selected_category_and_redirects(self, delay, form_class):
        task_id = "0b5940f9-5e4d-416a-bbd6-758c3dd84cc5"
        delay.return_value.id = task_id
        form_class.return_value.cleaned_data = {
            "category": SimpleNamespace(pk=5), "variants": [1, 4], "mode": "missing",
        }
        response = category_audio(RequestFactory().post("/", {}))
        delay.assert_called_once_with(5, [1, 4], "missing")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("category-audio-progress", args=[task_id]))

    @patch("logviewer.views.render")
    @patch("logviewer.views.CategoryAudioForm")
    @patch("logviewer.views.generate_category_audio.delay", side_effect=OperationalError("offline"))
    def test_broker_failure_is_visible(self, delay, form_class, render):
        form_class.return_value.cleaned_data = {
            "category": SimpleNamespace(pk=5), "variants": [1], "mode": "missing",
        }
        with self.assertLogs("logviewer.views", level="ERROR"):
            category_audio(RequestFactory().post("/", {}))
        self.assertEqual(render.call_args.kwargs["status"], 503)
        form_class.return_value.add_error.assert_called_once()


class AnnouncementTableTests(SimpleTestCase):
    def test_track_and_playback_tables_link_all_four_variants(self):
        track = Track(pk=42)
        for table_class, record in (
            (TracksTable, track),
            (PlaybackTable, Playback(pk=100, trackid=track)),
        ):
            with self.subTest(table=table_class.__name__):
                row = next(iter(table_class([record]).rows))
                for num in (1, 2, 3, 4):
                    self.assertIn(
                        f"/media/42-{num}~0.mp3",
                        str(row.get_cell(f"announcement_{num}")),
                    )

    def test_new_playback_controls_are_excluded_from_data_exports(self):
        for table_class in (TracksTable, PlaybackTable):
            with self.subTest(table=table_class.__name__):
                headers = next(table_class([]).as_values())
                self.assertNotIn("Announcement 3", headers)
                self.assertNotIn("Announcement 4", headers)


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

    def test_year_announcement_variants_use_their_expected_text(self):
        for num, expected_text in (
            (3, "1966 music from Artist on K-V-W-J!"),
            (4, "That was Title from 1966 on J-95!"),
        ):
            with self.subTest(announcement=num):
                result = regenerate_single_audio.run(42, num)
                self.assertEqual(result["announcement"], num)
                self.assertEqual(
                    self.generate_audio.call_args.args,
                    (expected_text, 42, num),
                )
                self.generate_audio.reset_mock()

    def test_year_announcement_rejects_track_without_year(self):
        self.track.yearid = None

        with self.assertRaisesMessage(ValueError, "requires a year"):
            regenerate_single_audio.run(42, 3)

        self.generate_audio.assert_not_called()

    @patch("logviewer.views.get_object_or_404")
    @patch("logviewer.views.regenerate_single_audio.delay")
    def test_start_endpoint_queues_year_variant(self, delay, get_track):
        from .views import start_single_audio

        get_track.return_value = SimpleNamespace(pk=42, yearid_id=1966)
        delay.return_value.id = "task-id"
        response = start_single_audio(RequestFactory().post("/"), 42, 4)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content), {"task_id": "task-id"})
        delay.assert_called_once_with(42, 4)

    @patch("logviewer.views.get_object_or_404")
    @patch("logviewer.views.regenerate_single_audio.delay")
    def test_start_endpoint_rejects_year_variant_without_year(self, delay, get_track):
        from .views import start_single_audio

        get_track.return_value = SimpleNamespace(pk=42, yearid_id=None)
        response = start_single_audio(RequestFactory().post("/"), 42, 3)

        self.assertEqual(response.status_code, 400)
        self.assertIn(
            "requires a track year",
            json.loads(response.content)["error"],
        )
        delay.assert_not_called()
