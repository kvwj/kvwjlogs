import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from xml.sax.saxutils import escape

import requests
from django.db import DatabaseError, transaction
from openai import OpenAI

from .models import AnnouncementVoice


SPEECH_PROVIDERS = (
    ("openai", "OpenAI"),
    ("microsoft", "Microsoft Speech Service"),
)
EIGHTIES_SHOW_PROVIDER = "microsoft"
EIGHTIES_SHOW_VOICE = "emma_dragon_hd"
ANNOUNCEMENT_SHIFT_CHOICES = (
    ("female", "Morning (Nova)"),
    ("male", "Afternoon (Andrew)"),
    ("male_2", "Night (Davis)"),
    ("eighties", "80s show (Emma)"),
)
SPEECH_VOICES = {
    "openai": (("nova", "Nova (female)"), ("ash", "Ash (male)")),
    "microsoft": (
        ("nova_dragon_hd", "Nova DragonHD (female)"),
        ("ava_dragon_hd", "Ava DragonHD (female)"),
        ("emma_dragon_hd", "Emma DragonHD (female)"),
        ("andrew_dragon_hd", "Andrew DragonHD (male)"),
        ("brian_dragon_hd", "Brian DragonHD (male)"),
        ("davis_dragon_hd", "Davis DragonHD (male)"),
    ),
}
SPEECH_VOICE_GENDERS = {
    "openai": {"nova": "female", "ash": "male"},
    "microsoft": {
        "nova_dragon_hd": "female", "ava_dragon_hd": "female", "emma_dragon_hd": "female",
        "andrew_dragon_hd": "male", "brian_dragon_hd": "male", "davis_dragon_hd": "male",
    },
}
LEGACY_MICROSOFT_VOICES = (
    ("nova", "Nova Turbo (female)"),
    ("shimmer", "Shimmer Turbo (female)"),
    ("echo", "Echo Turbo (male)"),
    ("christopher", "Christopher standard (male)"),
    ("onyx", "Onyx Turbo (male)"),
)
MICROSOFT_VOICES = {
    "nova": "en-US-NovaTurboMultilingualNeural",
    "shimmer": "en-US-ShimmerTurboMultilingualNeural",
    "echo": "en-US-EchoTurboMultilingualNeural",
    "christopher": "en-US-ChristopherNeural",
    "onyx": "en-US-OnyxTurboMultilingualNeural",
    "nova_dragon_hd": "en-US-Nova:DragonHDLatestNeural",
    "ava_dragon_hd": "en-US-Ava:DragonHDLatestNeural",
    "emma_dragon_hd": "en-US-Emma:DragonHDLatestNeural",
    "andrew_dragon_hd": "en-US-Andrew:DragonHDLatestNeural",
    "brian_dragon_hd": "en-US-Brian:DragonHDLatestNeural",
    "davis_dragon_hd": "en-US-Davis:DragonHDLatestNeural",
}


def validate_speech_options(provider, voice, *, allow_legacy=False, gender=None):
    if provider not in SPEECH_VOICES:
        raise ValueError("Unsupported speech provider.")
    valid_voices = dict(SPEECH_VOICES[provider])
    if allow_legacy and provider == "microsoft":
        valid_voices.update(LEGACY_MICROSOFT_VOICES)
    if voice not in valid_voices:
        raise ValueError("Unsupported voice for the selected speech provider.")
    if gender and SPEECH_VOICE_GENDERS[provider].get(voice) != gender:
        raise ValueError(f"Select a {gender} voice for this announcement set.")


def announcement_path(trackid, num):
    return Path('/mnt/announcements/media') / f"{trackid}-{num}~0.mp3"


def speech_title(title, provider, voice):
    if provider == "microsoft" and voice in dict(LEGACY_MICROSOFT_VOICES):
        return title.capitalize()
    return title


def synthesize_audio(text, speech_file_path, provider="openai", voice="nova"):
    validate_speech_options(provider, voice, allow_legacy=True)

    if provider == "microsoft":
        key = os.environ.get("AZURE_SPEECH_KEY")
        if not key:
            raise ValueError("AZURE_SPEECH_KEY is required for Microsoft Speech Service.")
        region = os.environ.get("AZURE_SPEECH_REGION")
        if not region:
            raise ValueError("AZURE_SPEECH_REGION is required for Microsoft Speech Service.")
        if text.endswith(" on J-95!"):
            spoken_text = (
                escape(text.removesuffix("J-95!"))
                + '<sub alias="Jay ninety five">J-95</sub>!'
            )
        else:
            spoken_text = escape(text)
        ssml = (
            '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
            'xml:lang="en-US">'
            f'<voice name="{MICROSOFT_VOICES[voice]}">{spoken_text}</voice>'
            '</speak>'
        )
        response = requests.post(
            f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1",
            headers={
                "Ocp-Apim-Subscription-Key": key,
                "Content-Type": "application/ssml+xml",
                "X-Microsoft-OutputFormat": "audio-48khz-192kbitrate-mono-mp3",
            },
            data=ssml.encode("utf-8"),
            timeout=60,
        )
        response.raise_for_status()
        if not response.content:
            raise ValueError("Microsoft Speech Service returned empty audio.")
        speech_file_path.write_bytes(response.content)
        return

    client = OpenAI()
    with client.audio.speech.with_streaming_response.create(
        model="gpt-4o-mini-tts",
        voice=voice,
        input=text,
        instructions="Speak with excitement like a radio DJ",
    ) as response:
        response.stream_to_file(speech_file_path)


def regenerate_audio(text, trackid, num, provider="openai", voice="nova"):
    speech_file_path = announcement_path(trackid, num)
    with NamedTemporaryFile(
        dir=speech_file_path.parent, suffix=".mp3", delete=False,
    ) as temporary:
        temporary_path = Path(temporary.name)
    try:
        synthesize_audio(text, temporary_path, provider, voice)
        temporary_path.chmod(0o644)
        record, _ = AnnouncementVoice.objects.update_or_create(
            track_id=trackid, variant=num, defaults={"provider": "", "voice": ""},
        )
        try:
            with transaction.atomic():
                record = AnnouncementVoice.objects.select_for_update().get(pk=record.pk)
                temporary_path.replace(speech_file_path)
                record.provider = provider
                record.voice = voice
                record.save(update_fields=["provider", "voice"])
        except (DatabaseError, OSError):
            AnnouncementVoice.objects.filter(pk=record.pk).update(provider="", voice="")
            raise
    finally:
        temporary_path.unlink(missing_ok=True)