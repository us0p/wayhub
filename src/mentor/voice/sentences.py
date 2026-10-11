"""Splits the streamed reply into sentences for TTS, so speech starts after the first sentence
instead of after the whole reply."""

import re

# Sentence-ending punctuation (optionally closed by quotes/brackets) followed by whitespace,
# or a line break. "3.5" or "mentor.com.br" have no whitespace after the dot, so they stay whole.
_BOUNDARY = re.compile("[.!?\u2026]+[\"'\u201d\u2019)\\]]*\\s+|\n+")


class SentenceSplitter:
    MAX_CHARS = 200  # a run without punctuation is cut at a space past this length

    def __init__(self) -> None:
        self._buffer = ""

    def feed(self, text: str) -> list[str]:
        self._buffer += text
        out: list[str] = []
        start = 0
        for match in _BOUNDARY.finditer(self._buffer):
            out.append(self._buffer[start : match.end()])
            start = match.end()
        self._buffer = self._buffer[start:]
        while len(self._buffer) > self.MAX_CHARS:
            cut = self._buffer.rfind(" ", 0, self.MAX_CHARS + 1)
            if cut <= 0:
                cut = self.MAX_CHARS
            out.append(self._buffer[:cut])
            self._buffer = self._buffer[cut:]
        return [s for part in out if (s := part.strip())]

    def flush(self) -> str | None:
        rest, self._buffer = self._buffer.strip(), ""
        return rest or None
