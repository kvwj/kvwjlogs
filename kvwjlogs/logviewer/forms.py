# forms.py
from django import forms
from django.core.exceptions import ValidationError
from django.forms.widgets import TextInput
from .models import Track, Artist, Category
from .audio import (
    ANNOUNCEMENT_SHIFT_CHOICES, EIGHTIES_SHOW_PROVIDER, EIGHTIES_SHOW_VOICE,
    SPEECH_PROVIDERS, SPEECH_VOICES, SPEECH_VOICE_GENDERS,
)

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


class SpeechSelectionForm(forms.Form):
    provider = forms.ChoiceField(
        label="Speech service",
        choices=SPEECH_PROVIDERS,
        initial="openai",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    voice = forms.ChoiceField(
        choices=SPEECH_VOICES["openai"],
        initial="nova",
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    def __init__(self, *args, **kwargs):
        self.gender = kwargs.pop("gender", None)
        super().__init__(*args, **kwargs)
        provider = (
            self.data.get(self.add_prefix("provider"))
            if self.is_bound
            else self.initial.get("provider", "openai")
        )
        self.fields["voice"].choices = [
            (voice, label) for voice, label in SPEECH_VOICES.get(provider, ())
            if not self.gender or SPEECH_VOICE_GENDERS[provider][voice] == self.gender
        ]


class AnnouncementShiftForm(forms.Form):
    female_shift_start = forms.TimeField(
        label="Shift 1 starts",
        input_formats=["%H:%M"],
        widget=forms.TimeInput(format="%H:%M", attrs={"type": "time", "class": "form-control"}),
    )
    male_shift_start = forms.TimeField(
        label="Shift 2 starts",
        input_formats=["%H:%M"],
        widget=forms.TimeInput(format="%H:%M", attrs={"type": "time", "class": "form-control"}),
    )
    second_male_shift_start = forms.TimeField(
        label="Shift 3 starts",
        input_formats=["%H:%M"],
        widget=forms.TimeInput(format="%H:%M", attrs={"type": "time", "class": "form-control"}),
    )
    eighties_show_start = forms.TimeField(
        label="Saturday 80s show starts",
        input_formats=["%H:%M"],
        widget=forms.TimeInput(format="%H:%M", attrs={"type": "time", "class": "form-control"}),
    )
    eighties_show_end = forms.TimeField(
        label="80s show ends",
        input_formats=["%H:%M"],
        widget=forms.TimeInput(format="%H:%M", attrs={"type": "time", "class": "form-control"}),
    )

    def clean(self):
        cleaned = super().clean()
        times = [cleaned.get(field) for field in (
            "female_shift_start", "male_shift_start", "second_male_shift_start",
        )]
        if None not in times and len(set(times)) != len(times):
            raise ValidationError("The three shifts must start at different times.")
        show_start = cleaned.get("eighties_show_start")
        show_end = cleaned.get("eighties_show_end")
        if show_start is not None and show_start == show_end:
            raise ValidationError("The 80s show must start and end at different times.")
        return cleaned


class CategoryAudioForm(SpeechSelectionForm):
    field_order = ("category", "provider", "voice", "variants", "mode")
    category = forms.ModelChoiceField(
        queryset=Category.objects.exclude(category="Sunday").order_by("category", "pk"),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    variants = forms.TypedMultipleChoiceField(
        label="Announcements",
        choices=(
            (1, "Shift 1: artist with title on J-95"),
            (2, "Shift 1: title, that's artist on K-V-W-J"),
            (3, "Shift 1: year music from artist on K-V-W-J"),
            (4, "Shift 1: that was title from year on J-95"),
            (5, "Shift 2: artist with title on J-95"),
            (6, "Shift 2: title, that's artist on K-V-W-J"),
            (7, "Shift 2: year music from artist on K-V-W-J"),
            (8, "Shift 2: that was title from year on J-95"),
            (9, "Shift 3: artist with title on J-95"),
            (10, "Shift 3: title, that's artist on K-V-W-J"),
            (11, "Shift 3: year music from artist on K-V-W-J"),
            (12, "Shift 3: that was title from year on J-95"),
            (13, "80s show: artist with title on J-95"),
            (14, "80s show: title, that's artist on K-V-W-J"),
            (15, "80s show: year music from artist on K-V-W-J"),
            (16, "80s show: that was title from year on J-95"),
        ),
        coerce=int,
        initial=[1, 2, 3, 4],
        widget=forms.CheckboxSelectMultiple,
    )
    mode = forms.ChoiceField(
        choices=(
            ("missing", "Generate missing files"),
            ("regenerate", "Regenerate all selected files"),
        ),
        initial="missing",
        widget=forms.RadioSelect,
    )

    def clean(self):
        cleaned = super().clean()
        variants = cleaned.get("variants", [])
        provider, voice = cleaned.get("provider"), cleaned.get("voice")
        if variants and provider and voice:
            sets = {(num - 1) // 4 for num in variants}
            if len(sets) > 1:
                self.add_error("variants", "Select announcements from only one shift per job.")
            else:
                voice_set = next(iter(sets))
                gender = "female" if voice_set in (0, 3) else "male"
                if voice_set == 3 and (provider, voice) != (EIGHTIES_SHOW_PROVIDER, EIGHTIES_SHOW_VOICE):
                    self.add_error("voice", "Select Microsoft Emma DragonHD for 80s show announcements.")
                elif SPEECH_VOICE_GENDERS[provider][voice] != gender:
                    self.add_error("voice", f"Select a {gender} voice for these announcements.")
        return cleaned


class ArtistAudioForm(forms.Form):
    voice_set = forms.ChoiceField(
        label="Announcement shift",
        choices=ANNOUNCEMENT_SHIFT_CHOICES,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    variants = forms.TypedMultipleChoiceField(
        label="Announcement varieties",
        choices=(
            (1, "Artist with title on J-95"),
            (2, "Title, that's artist on K-V-W-J"),
            (3, "Year music from artist on K-V-W-J"),
            (4, "That was title from year on J-95"),
        ),
        coerce=int,
        initial=[1, 2],
        widget=forms.CheckboxSelectMultiple,
    )
    mode = forms.ChoiceField(
        choices=(
            ("missing", "Generate missing files"),
            ("regenerate", "Regenerate all selected files"),
        ),
        initial="regenerate",
        widget=forms.RadioSelect,
    )

    def __init__(self, *args, include_eighties=False, **kwargs):
        super().__init__(*args, **kwargs)
        if not include_eighties:
            self.fields["voice_set"].choices = [
                (value, label) for value, label in ANNOUNCEMENT_SHIFT_CHOICES
                if value != "eighties"
            ]
