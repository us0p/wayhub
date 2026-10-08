"""GoogleSTT/GoogleTTS against stub gRPC clients: request streams and response mapping."""

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any, cast

import pytest
from google.api_core import exceptions as gexc
from google.cloud import speech_v2, texttospeech_v1

from mentor.ai.adapters.google_speech import MAX_AUDIO_REQUEST, GoogleSTT, GoogleTTS
from mentor.ai.ports import STT_SAMPLE_RATE, TTS_SAMPLE_RATE, AIProviderError, Transcript


async def _aiter[T](*items: T) -> AsyncIterator[T]:
    for item in items:
        yield item


class StubStream:
    """Consumes the whole request stream, then replies with the scripted responses."""

    def __init__(self, responses: list[Any]) -> None:
        self.responses = responses
        self.requests: list[Any] = []

    async def __call__(self, requests: AsyncIterator[Any]) -> AsyncIterator[Any]:
        self.requests = [r async for r in requests]

        async def iterate() -> AsyncIterator[Any]:
            for response in self.responses:
                if isinstance(response, Exception):
                    raise response
                yield response

        return iterate()


def _stt_response(*results: tuple[str, bool]) -> speech_v2.StreamingRecognizeResponse:
    alternative = speech_v2.SpeechRecognitionAlternative
    return speech_v2.StreamingRecognizeResponse(
        results=[
            speech_v2.StreamingRecognitionResult(
                alternatives=[alternative(transcript=text)] if text else [], is_final=final
            )
            for text, final in results
        ]
    )


def _stt(stream: StubStream) -> GoogleSTT:
    client = cast(speech_v2.SpeechAsyncClient, SimpleNamespace(streaming_recognize=stream))
    return GoogleSTT("proj", "us", "chirp_3", client=client)


async def test_stt_sends_config_then_split_audio() -> None:
    stream = StubStream([])
    audio = b"\x01" * (MAX_AUDIO_REQUEST + 10)

    [_ async for _ in _stt(stream).stream(_aiter(audio, b"\x02"), language="en-US")]

    first, *rest = stream.requests
    assert first.recognizer == "projects/proj/locations/us/recognizers/_"
    config = first.streaming_config.config
    assert config.model == "chirp_3"
    assert list(config.language_codes) == ["en-US"]
    assert config.explicit_decoding_config.sample_rate_hertz == STT_SAMPLE_RATE
    assert first.streaming_config.streaming_features.interim_results
    assert [len(r.audio) for r in rest] == [MAX_AUDIO_REQUEST, 10, 1]
    assert b"".join(r.audio for r in rest) == audio + b"\x02"


async def test_stt_maps_interim_and_final_results_and_skips_empty() -> None:
    stream = StubStream(
        [_stt_response(("meu", False)), _stt_response(("", False), ("meu nome é Ana", True))]
    )

    out = [t async for t in _stt(stream).stream(_aiter(b"\x00\x00"))]

    assert out == [Transcript("meu", False), Transcript("meu nome é Ana", True)]


async def test_stt_errors_become_provider_errors() -> None:
    stream = StubStream([gexc.ResourceExhausted("quota")])

    with pytest.raises(AIProviderError, match="Speech-to-Text"):
        [_ async for _ in _stt(stream).stream(_aiter(b"\x00\x00"))]


def _tts(stream: StubStream) -> GoogleTTS:
    client = cast(
        texttospeech_v1.TextToSpeechAsyncClient, SimpleNamespace(streaming_synthesize=stream)
    )
    return GoogleTTS("pt-BR-Chirp3-HD-Kore", client=client)


async def test_tts_sends_voice_config_then_non_blank_text_and_yields_audio() -> None:
    stream = StubStream(
        [
            texttospeech_v1.StreamingSynthesizeResponse(audio_content=b"\x01\x02"),
            texttospeech_v1.StreamingSynthesizeResponse(audio_content=b""),
            texttospeech_v1.StreamingSynthesizeResponse(audio_content=b"\x03\x04"),
        ]
    )

    audio = [a async for a in _tts(stream).stream(_aiter("Olá. ", " ", "Tudo bem?"))]

    assert audio == [b"\x01\x02", b"\x03\x04"]
    first, *rest = stream.requests
    voice = first.streaming_config.voice
    assert (voice.name, voice.language_code) == ("pt-BR-Chirp3-HD-Kore", "pt-BR")
    audio_config = first.streaming_config.streaming_audio_config
    assert audio_config.audio_encoding == texttospeech_v1.AudioEncoding.PCM
    assert audio_config.sample_rate_hertz == TTS_SAMPLE_RATE
    assert [r.input.text for r in rest] == ["Olá. ", "Tudo bem?"]


async def test_tts_voice_override_sets_its_language() -> None:
    stream = StubStream([])

    [_ async for _ in _tts(stream).stream(_aiter("Hi"), voice="en-US-Chirp3-HD-Charon")]

    voice = stream.requests[0].streaming_config.voice
    assert (voice.name, voice.language_code) == ("en-US-Chirp3-HD-Charon", "en-US")
