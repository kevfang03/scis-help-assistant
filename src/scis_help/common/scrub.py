"""Scrub a user's message before retrieval, the LLM call and any log (ARCHITECTURE.md D7).

The rules, in order:

1. password phrases -> ``[PASSWORD]``: ``password``/``passwd``/``passcode``/``pass``/``pw``
   (any case) then ``is``/``was``/``:``/``=`` and the next token (or a quoted string), or with
   no separator when the next token holds a digit or a symbol (``my pw Hunter2!``). The keyword
   stays so the question still reads "my password [PASSWORD]". A few ordinary words after
   ``is`` ("is not working", "is expired") are left alone; a bare plain word with no separator
   ("my password sunshine") is not caught, a known gap;
2. email addresses -> ``[EMAIL]``;
3. US phone numbers, with or without an extension -> ``[PHONE]``;
4. 8-digit Drexel IDs -> ``[ID]``;
5. Drexel user ids, 2-3 letters then 2-4 digits (``abc123``) -> ``[USER]``. Course codes written
   with a space or dash (``CS 265``, ``CS-265``) stay; ``cs265`` written solid is over-scrubbed,
   which is accepted.

Over-scrubbing is fine; under-scrubbing is not. Nothing here logs, prints or raises with the text.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

# The counted kinds, in rule order.
KINDS = ("password", "email", "phone", "id", "user")

_KEYWORD = r"\b(?:password|passwd|passcode|pass|pw)\b"
_QUOTED = r"\"[^\"\n]*\"|'[^'\n]*'|“[^”\n]*”"
_TOKEN = r"[^\s\"'“”]+"
# Words after "is" that describe the password rather than spell it.
_NOT_SECRETS = frozenset(
    "not wrong expired incorrect invalid locked correct right still being too the a an my no"
    " working reset changed rejected required needed case same old new empty blank".split()
)
_PASSWORD_SEPARATED = re.compile(
    rf"(?P<lead>{_KEYWORD}\s*(?:(?:is|was)\s+|[:=]\s*))(?P<secret>{_QUOTED}|{_TOKEN})",
    re.IGNORECASE,
)
_PASSWORD_BARE = re.compile(
    rf"(?P<lead>{_KEYWORD}\s+)(?P<secret>(?=[^\s]*[\d!@#$%^&*()_+=\[\]{{}}<>?/\\|~])[^\s\"'“”]+)",
    re.IGNORECASE,
)
_EMAIL = re.compile(r"[\w.%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")
_PHONE = re.compile(
    r"(?<![\w+])(?:\+?1[ \t.\-]?)?(?:\(\d{3}\)[ \t]?|\d{3}[ \t.\-]?)\d{3}[ \t.\-]?\d{4}"
    r"(?:[ \t]*(?:x|ext\.?|extension)[ \t]*\d{1,6})?(?!\w)",
    re.IGNORECASE,
)
_ID = re.compile(r"\b\d{8}\b")
_USER = re.compile(r"\b[A-Za-z]{2,3}\d{2,4}\b")


@dataclass(frozen=True, slots=True)
class Scrubbed:
    """A scrubbed message and how many of each kind were replaced (counts only)."""

    text: str
    replaced: Mapping[str, int]  # {"password": 0, "email": 0, "phone": 0, "id": 0, "user": 0}

    @property
    def any(self) -> bool:
        return any(self.replaced.values())

    def __repr__(self) -> str:
        counts = ", ".join(f"{kind}={count}" for kind, count in self.replaced.items())
        return f"Scrubbed(chars={len(self.text)}, {counts})"


def scrub(text: str) -> Scrubbed:
    """``text`` with passwords, emails, phone numbers, IDs and user ids replaced by
    placeholders, in the order of the module docstring."""
    counts = dict.fromkeys(KINDS, 0)

    def password(match: re.Match[str]) -> str:
        secret = match["secret"]
        if secret.lower().rstrip(".,!?;") in _NOT_SECRETS or secret.startswith("[PASSWORD"):
            return match[0]
        counts["password"] += 1
        return f"{match['lead']}[PASSWORD]"

    def replace(kind: str, placeholder: str):
        def _replace(match: re.Match[str]) -> str:
            counts[kind] += 1
            return placeholder

        return _replace

    text = _PASSWORD_SEPARATED.sub(password, text)
    text = _PASSWORD_BARE.sub(password, text)
    text = _EMAIL.sub(replace("email", "[EMAIL]"), text)
    text = _PHONE.sub(replace("phone", "[PHONE]"), text)
    text = _ID.sub(replace("id", "[ID]"), text)
    text = _USER.sub(replace("user", "[USER]"), text)
    return Scrubbed(text, MappingProxyType(counts))
