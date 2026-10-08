"""i18n layer (D3). UI is pt-BR only for now: source strings are written in pt-BR and
wrapped in ``_()`` so a catalog for other locales can be added later without touching templates."""

import gettext

_translations: gettext.NullTranslations = gettext.NullTranslations()


def gettext_(message: str) -> str:
    return _translations.gettext(message)


def ngettext_(singular: str, plural: str, n: int) -> str:
    return _translations.ngettext(singular, plural, n)
