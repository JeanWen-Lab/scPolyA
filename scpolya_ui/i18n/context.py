from __future__ import annotations

from dataclasses import dataclass

from .catalog import DEFAULT_LOCALE, available_locales, translate


def normalize_locale(locale: str | None) -> str:
    if not locale:
        return DEFAULT_LOCALE
    locale = str(locale).strip()
    locales = set(available_locales())
    if locale in locales:
        return locale
    short = locale.split("-", 1)[0]
    if short in locales:
        return short
    return DEFAULT_LOCALE


@dataclass
class LocaleContext:
    locale: str = DEFAULT_LOCALE

    def set_locale(self, locale: str | None) -> None:
        self.locale = normalize_locale(locale)

    def t(self, key: str, **params) -> str:
        return translate(self.locale, key, **params)
