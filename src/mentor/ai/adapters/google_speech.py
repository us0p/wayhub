"""Google Cloud Speech-to-Text v2 (Chirp 3) and Text-to-Speech streaming adapters (D7, D46).

Credentials come from Application Default Credentials. The gRPC clients are created lazily on
first use, so building the registry never needs credentials (tests, text-only flows).

STT denoises the audio before recognition unless `denoise=False` (`GOOGLE_STT_DENOISE`, D67).

Not handled here: Speech v2 caps a stream at about 5 minutes; the voice pipeline (M6) rotates
streams by ending `audio` and starting a new `stream()` call.
"""

from collections.abc import AsyncIterable, AsyncIterator
from typing import Literal

from google.api_core import exceptions as gexc
from google.api_core.client_options import ClientOptions
from google.cloud import speech_v2, texttospeech_v1

from mentor.ai.ports import (
    DEFAULT_LANGUAGE,
    STT_SAMPLE_RATE,
    TTS_SAMPLE_RATE,
    AIProviderError,
    Transcript,
)

MAX_AUDIO_REQUEST = 15_360  # bytes per StreamingRecognizeRequest (~0.5 s at 16 kHz)

# How much silence Chirp 3 waits for before finalizing a result (D69): `standard` waits to be
# sure the utterance is complete; `short` suits single sentences; `supershort` finalizes as soon
# as speech ends (lowest latency, may cut off a pause mid-sentence).
type Endpointing = Literal["standard", "short", "supershort"]
_Sensitivity = speech_v2.StreamingRecognitionFeatures.EndpointingSensitivity
ENDPOINTING: dict[str, int] = {
    "standard": _Sensitivity.ENDPOINTING_SENSITIVITY_STANDARD,
    "short": _Sensitivity.ENDPOINTING_SENSITIVITY_SHORT,
    "supershort": _Sensitivity.ENDPOINTING_SENSITIVITY_SUPERSHORT,
}


def _provider_error(service: str, exc: gexc.GoogleAPIError) -> AIProviderError:
    code = getattr(exc, "code", None)
    return AIProviderError(f"Google {service} error {code or type(exc).__name__}")


class GoogleSTT:
    def __init__(
        self,
        project: str,
        location: str,
        model: str,
        client: speech_v2.SpeechAsyncClient | None = None,
        *,
        denoise: bool = True,
        endpointing: Endpointing = "short",
    ) -> None:
        self._recognizer = f"projects/{project}/locations/{location}/recognizers/_"
        self._project = project
        self._location = location
        self._model = model
        self._client = client
        self._denoise = denoise
        self._endpointing = ENDPOINTING[endpointing]

    def _get_client(self) -> speech_v2.SpeechAsyncClient:
        if self._client is None:
            endpoint = (
                "speech.googleapis.com"
                if self._location == "global"
                else f"{self._location}-speech.googleapis.com"
            )
            self._client = speech_v2.SpeechAsyncClient(
                client_options=ClientOptions(api_endpoint=endpoint, quota_project_id=self._project)
            )
        return self._client

    def _config_request(self, language: str) -> speech_v2.StreamingRecognizeRequest:
        config = speech_v2.RecognitionConfig(
            explicit_decoding_config=speech_v2.ExplicitDecodingConfig(
                encoding=speech_v2.ExplicitDecodingConfig.AudioEncoding.LINEAR16,
                sample_rate_hertz=STT_SAMPLE_RATE,
                audio_channel_count=1,
            ),
            language_codes=[language],
            model=self._model,
            features=speech_v2.RecognitionFeatures(enable_automatic_punctuation=True),
            # Removes background music and noise (rain, traffic); not other people's voices.
            # The SNR threshold is deprecated on Chirp 3 and must stay 0 (D67).
            denoiser_config=(
                speech_v2.DenoiserConfig(denoise_audio=True, snr_threshold=0.0)
                if self._denoise
                else None
            ),
        )
        return speech_v2.StreamingRecognizeRequest(
            recognizer=self._recognizer,
            streaming_config=speech_v2.StreamingRecognitionConfig(
                config=config,
                streaming_features=speech_v2.StreamingRecognitionFeatures(
                    interim_results=True,  # live transcript and barge-in
                    endpointing_sensitivity=self._endpointing,
                ),
            ),
        )

    async def stream(
        self, audio: AsyncIterable[bytes], *, language: str = DEFAULT_LANGUAGE
    ) -> AsyncIterator[Transcript]:
        async def requests() -> AsyncIterator[speech_v2.StreamingRecognizeRequest]:
            yield self._config_request(language)
            async for chunk in audio:
                for start in range(0, len(chunk), MAX_AUDIO_REQUEST):
                    yield speech_v2.StreamingRecognizeRequest(
                        audio=chunk[start : start + MAX_AUDIO_REQUEST]
                    )

        try:
            responses = await self._get_client().streaming_recognize(requests=requests())
            async for response in responses:
                for result in response.results:
                    if result.alternatives and result.alternatives[0].transcript:
                        yield Transcript(
                            text=result.alternatives[0].transcript, is_final=result.is_final
                        )
        except gexc.GoogleAPIError as exc:
            raise _provider_error("Speech-to-Text", exc) from exc


class GoogleTTS:
    """Streaming synthesis; only Chirp 3 HD voices support it."""

    def __init__(
        self, voice: str, client: texttospeech_v1.TextToSpeechAsyncClient | None = None
    ) -> None:
        self._voice = voice
        self._client = client

    def _get_client(self) -> texttospeech_v1.TextToSpeechAsyncClient:
        if self._client is None:
            self._client = texttospeech_v1.TextToSpeechAsyncClient()
        return self._client

    @staticmethod
    def _config_request(voice: str) -> texttospeech_v1.StreamingSynthesizeRequest:
        language = "-".join(voice.split("-")[:2])  # pt-BR-Chirp3-HD-Kore → pt-BR
        return texttospeech_v1.StreamingSynthesizeRequest(
            streaming_config=texttospeech_v1.StreamingSynthesizeConfig(
                voice=texttospeech_v1.VoiceSelectionParams(name=voice, language_code=language),
                streaming_audio_config=texttospeech_v1.StreamingAudioConfig(
                    audio_encoding=texttospeech_v1.AudioEncoding.PCM,
                    sample_rate_hertz=TTS_SAMPLE_RATE,
                ),
            )
        )

    async def stream(
        self, text: AsyncIterable[str], *, voice: str | None = None
    ) -> AsyncIterator[bytes]:
        async def requests() -> AsyncIterator[texttospeech_v1.StreamingSynthesizeRequest]:
            yield self._config_request(voice or self._voice)
            async for chunk in text:
                if chunk.strip():
                    yield texttospeech_v1.StreamingSynthesizeRequest(
                        input=texttospeech_v1.StreamingSynthesisInput(text=chunk)
                    )

        try:
            responses = await self._get_client().streaming_synthesize(requests=requests())
            async for response in responses:
                if response.audio_content:
                    yield response.audio_content
        except gexc.GoogleAPIError as exc:
            raise _provider_error("Text-to-Speech", exc) from exc
