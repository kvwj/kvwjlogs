# forms.py
from django import forms
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
