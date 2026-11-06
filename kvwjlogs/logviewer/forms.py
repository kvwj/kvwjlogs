# forms.py
from django import forms
from django.core.exceptions import ValidationError
from django.forms.widgets import TextInput
from .models import Track, Artist

class TrackDisplayForm(forms.ModelForm):
    track_name = forms.CharField(disabled=True)  # read-only

    class Meta:
        model = Track
        fields = ["track_display_name", "track_name"]
        widgets = {
            "track_name": TextInput(attrs={"disabled": True}),
        }

class ArtistDisplayForm(forms.ModelForm):
    artist_name = forms.CharField(disabled=True)  # read-only

    class Meta:
        model = Artist
        fields = ["artist_display_name", "artist_name"]
        widgets = {
            "artist_name": TextInput(attrs={"disabled": True}),
        }


class RdsOverrideForm(forms.Form):
    text = forms.CharField(
        label="Text to display",
        max_length=64,
        strip=True,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "autocomplete": "off",
                "maxlength": 64,
            }
        ),
    )
    lifetime = forms.ChoiceField(
        label="Override duration",
        choices=(
            ("until_next_song", "Until the next playback update"),
            ("until_cleared", "Until cleared manually"),
        ),
        initial="until_next_song",
        widget=forms.RadioSelect,
    )

    def clean_text(self):
        text = self.cleaned_data["text"]
        if any(ord(character) < 32 or ord(character) == 127 for character in text):
            raise ValidationError("Use printable characters without line breaks.")
        return text
