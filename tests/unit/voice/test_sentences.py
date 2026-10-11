from mentor.voice.sentences import SentenceSplitter


def _split(*chunks: str) -> list[str]:
    splitter = SentenceSplitter()
    out = [s for chunk in chunks for s in splitter.feed(chunk)]
    if rest := splitter.flush():
        out.append(rest)
    return out


def test_sentences_are_released_as_soon_as_they_end() -> None:
    splitter = SentenceSplitter()

    assert splitter.feed("Que bom! Você trabalha ") == ["Que bom!"]
    assert splitter.feed("com Python? Me conta") == ["Você trabalha com Python?"]
    assert splitter.flush() == "Me conta"
    assert splitter.flush() is None


def test_a_period_inside_a_number_or_word_does_not_split() -> None:
    assert _split("Foram 3.5 anos no ", "site mentor.com.br, certo?") == [
        "Foram 3.5 anos no site mentor.com.br, certo?"
    ]


def test_line_breaks_and_closing_quotes_end_sentences() -> None:
    assert _split('Ele disse "ótimo." Depois\nveio isso') == [
        'Ele disse "ótimo."',
        "Depois",
        "veio isso",
    ]


def test_a_long_run_without_punctuation_is_cut_at_a_space() -> None:
    words = " ".join(["palavra"] * 60)

    parts = _split(words)

    assert len(parts) > 1
    assert all(len(p) <= SentenceSplitter.MAX_CHARS for p in parts)
    assert " ".join(parts) == words


def test_blank_input_yields_nothing() -> None:
    assert _split("  ", "\n") == []
