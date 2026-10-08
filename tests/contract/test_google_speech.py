import array
from collections.abc import AsyncIterator

import pytest

from mentor.ai.adapters.google_speech import GoogleSTT, GoogleTTS
from mentor.ai.ports import STT_SAMPLE_RATE, TTS_SAMPLE_RATE

pytestmark = pytest.mark.contract


async def _aiter[T](*items: T) -> AsyncIterator[T]:
    for item in items:
        yield item


def _resample(pcm: bytes, src: int, dst: int) -> bytes:
    """Nearest-sample resampling of PCM16 mono; good enough for a speech round trip."""
    samples = array.array("h", pcm)
    count = len(samples) * dst // src
    return array.array("h", (samples[i * src // dst] for i in range(count))).tobytes()


async def _chunks(pcm: bytes, size: int = 3_200) -> AsyncIterator[bytes]:
    for start in range(0, len(pcm), size):  # 100 ms at 16 kHz
        yield pcm[start : start + size]


async def test_tts_then_stt_round_trip(tts: GoogleTTS, stt: GoogleSTT) -> None:
    audio = b"".join(
        [a async for a in tts.stream(_aiter("Olá, meu nome é Ana ", "e eu trabalho com Python."))]
    )

    # ~2 s of speech at 24 kHz PCM16 is ~96 KB; anything tiny means no speech came back.
    assert len(audio) > TTS_SAMPLE_RATE

    pcm16k = _resample(audio, TTS_SAMPLE_RATE, STT_SAMPLE_RATE)
    transcripts = [t async for t in stt.stream(_chunks(pcm16k))]

    final = " ".join(t.text for t in transcripts if t.is_final).lower()
    assert "ana" in final
    assert "python" in final
