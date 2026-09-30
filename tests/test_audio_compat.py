"""Exercise the real decoder API: PyAV upgrades can break Whisper at runtime."""
import wave

from faster_whisper.audio import decode_audio


def test_whisper_can_decode_audio_with_installed_pyav(tmp_path):
    path = tmp_path / "silence.wav"
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\0\0" * 16000)
    samples = decode_audio(str(path))
    assert samples.shape == (16000,)
    assert (samples == 0).all()
