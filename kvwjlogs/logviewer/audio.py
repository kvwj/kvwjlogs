from pathlib import Path
from openai import OpenAI

def regenerate_audio(text, trackid, num):
    client = OpenAI()
    speech_file_path = Path(str('/mnt/announcements/media')) / Path(str(trackid) + '-' + str(num)+ "~0.mp3")

    with client.audio.speech.with_streaming_response.create(
        model="gpt-4o-mini-tts",
        voice="nova",
        input=text,
        instructions="Speak with excitement like a radio DJ",
    ) as response:
        response.stream_to_file(speech_file_path)