from django.test import TestCase
from django.urls import reverse

from .models import RdsDisplayState


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
