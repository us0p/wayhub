"""Log-safe descriptions of AI failures.

Exception messages from providers, LangChain's output parsers and pydantic validation can
contain the model's output or the user's own words (personal data, LGPD). Logs get the
exception types and the code locations where they were raised, never the messages.
"""

import traceback


def describe(exc: BaseException, depth: int = 4) -> str:
    """E.g. `OutputParserException@pydantic.py:42 <- ValidationError@main.py:7`."""
    parts: list[str] = []
    current: BaseException | None = exc
    while current is not None and len(parts) < depth:
        frames = traceback.extract_tb(current.__traceback__)
        where = f"@{frames[-1].filename.rsplit('/', 1)[-1]}:{frames[-1].lineno}" if frames else ""
        parts.append(f"{type(current).__name__}{where}")
        current = current.__cause__ or current.__context__
    return " <- ".join(parts)
