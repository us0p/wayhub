from mentor.voice.session import spoken_words


def test_fillers_and_punctuation_are_not_words() -> None:
    assert spoken_words("Hmm... ahn, HM!") == []


def test_short_real_answers_are_words() -> None:
    assert spoken_words("É, sim.") == ["é", "sim"]
    assert spoken_words("Hm, não sei") == ["não", "sei"]
