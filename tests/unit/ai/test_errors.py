from mentor.ai.errors import describe


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
