import pytest

from mentor.web.redirects import safe_next


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("/perfil", "/perfil"),
        ("/vagas?x=1", "/vagas?x=1"),
        (None, "/"),
        ("", "/"),
        ("https://evil.example", "/"),
        ("//evil.example", "/"),
        ("/\\evil.example", "/"),
        ("javascript:alert(1)", "/"),
    ],
)
def test_safe_next_only_allows_relative_paths(target: str | None, expected: str) -> None:
    assert safe_next(target) == expected
