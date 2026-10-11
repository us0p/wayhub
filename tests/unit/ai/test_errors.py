from mentor.ai.errors import describe
from mentor.ai.ports import AIProviderError


def test_describe_keeps_types_and_locations_but_never_messages() -> None:
    try:
        try:
            raise ValueError("Ana Souza, ana@example.com")  # e.g. model output in a parser error
        except ValueError as inner:
            raise RuntimeError("parse failed: Ana Souza") from inner
    except RuntimeError as exc:
        text = describe(exc)

    assert text.startswith("RuntimeError@test_errors.py:")
    assert "<- ValueError@test_errors.py:" in text
    assert "Ana" not in text and "@example.com" not in text


def test_an_adapter_can_opt_in_a_safe_detail() -> None:
    exc = AIProviderError("Google error", log_detail="TTS Aborted http=409")

    assert describe(exc) == "AIProviderError [TTS Aborted http=409]"
    assert describe(AIProviderError("Ana Souza")) == "AIProviderError"
