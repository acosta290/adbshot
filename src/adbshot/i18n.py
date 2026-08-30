"""Runtime translation.

Source strings are English, so English is always complete by construction and
can never be missing an entry. Other languages live in `translations.py` as
plain dictionaries keyed by the English source string -- adding a language is
one dict, with no build step, no .po workflow and no compiled catalogues to
install.

Placeholders are named rather than positional ({count}, not {}), so a
translator can reorder them freely without breaking the format call.
"""

from __future__ import annotations

import os

from .translations import CATALOGUES

AUTO = "auto"
DEFAULT = "en"

# Order matters: this is what the Preferences dropdown shows.
LANGUAGES: list[tuple[str, str]] = [
    (AUTO, "Automatic (system language)"),
    ("en", "English"),
    ("es", "Español"),
]

_catalogue: dict[str, str] = {}
_language = DEFAULT


def available() -> list[str]:
    return [code for code, _label in LANGUAGES]


def system_language() -> str:
    """The best supported match for the session locale, else English."""
    for name in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        value = os.environ.get(name)
        if not value:
            continue
        code = value.split(":")[0].split(".")[0].split("_")[0].lower()
        if code in CATALOGUES or code == "en":
            return code
    return DEFAULT


def set_language(code: str) -> str:
    """Select the active language. Returns the code actually applied."""
    global _catalogue, _language
    resolved = system_language() if code == AUTO else (code or DEFAULT)
    _catalogue = CATALOGUES.get(resolved, {})
    _language = resolved
    return resolved


def current() -> str:
    return _language


def gettext(text: str) -> str:
    return _catalogue.get(text, text)


# The conventional short alias, used everywhere in the UI modules.
_ = gettext
