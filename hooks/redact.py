"""Masking of token-shaped text (P1-09), for what the hooks write down or put in a context.

`redact(text)` replaces what has the shape of a credential with MASK: known token prefixes
(GitHub, GitLab, `sk-` API keys, Google, npm, PyPI, Hugging Face, Stripe, AWS key ids, Slack,
JWTs, a private key's first line), the value of a `name=value` / `name: value` pair whose
name says secret (password, token, api_key, ...), what follows `Bearer` or `Basic`, and the
password inside a URL. Commit hashes, session ids, paths and ordinary sentences are left
alone. The front piece of a cut token (a distinctive prefix and a few characters) is masked
too: callers cut text to a limit, and half a token is still half a secret.

A help, not a guarantee: a secret without a known shape is not seen. Keeping secrets out of
plans, branch names and task ids comes first.
"""

from __future__ import annotations

import re

MASK = "[가림]"
MIN_VALUE = 8  # a shorter value after `name=` is not treated as a secret
MAX_PASSES = 3  # a mask can complete another pattern once; then the text is stable

_PEM = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----")
_TOKEN = re.compile(
    r"\b(?:"
    # distinctive prefixes: any length, so the front piece of a cut token goes as well
    r"gh[pousr]_[A-Za-z0-9]{4,}"
    r"|github_pat_[A-Za-z0-9_]{4,}"
    r"|glpat-[A-Za-z0-9_-]{4,}"
    r"|xox[abprs]-[A-Za-z0-9-]{4,}"
    r"|hf_[A-Za-z0-9]{20,}"
    r"|npm_[A-Za-z0-9]{20,}"
    r"|pypi-[A-Za-z0-9_-]{20,}"
    r"|AIza[0-9A-Za-z_-]{30,}"
    r"|[sr]k_live_[A-Za-z0-9]{16,}"
    r"|sk-[A-Za-z0-9_-]{20,}"
    r"|(?:AKIA|ASIA)[0-9A-Z]{16}"
    r"|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
    r")"
)
_SCHEME = re.compile(
    r"\b(Bearer|Basic)\s+(?=[A-Za-z0-9._~+/=-]*[0-9+/=_.~-])[A-Za-z0-9._~+/=-]{8,}"
)
# names: a secret word, optionally a prefix before it (DB_, client_) and `_key` after it
_PAIR = re.compile(
    r"(?<![A-Za-z0-9])((?:[A-Za-z0-9]{1,20}[_-]){0,3}"
    r"(?:password|passwd|passphrase|pass|secret|token|credentials?"
    r"|api[_-]?key|access[_-]?key|private[_-]?key|authorization)"
    r"(?:[_-]key)?"
    r"[\"']?\s{0,3}[:=]\s{0,3})"
    r"([\"']?)([^\s\"',;\[\]]{" + str(MIN_VALUE) + r",})",
    re.IGNORECASE,
)
_URL = re.compile(r"(://[^/\s:@]{1,100}:)(?!\[가림\]@)([^@\s/]{1,200})(@)")


def _once(text: str) -> str:
    out = _PEM.sub(MASK, text)
    out = _TOKEN.sub(MASK, out)
    out = _SCHEME.sub(rf"\1 {MASK}", out)
    out = _PAIR.sub(rf"\1\2{MASK}", out)
    return _URL.sub(rf"\1{MASK}\3", out)


def redact(text: object) -> object:
    """`text` with credential-shaped parts replaced by MASK; anything but text unchanged.
    Applying it twice gives the same result as applying it once."""
    if not isinstance(text, str) or not text:
        return text
    out = text
    for _ in range(MAX_PASSES):
        again = _once(out)
        if again == out:
            break
        out = again
    return out
