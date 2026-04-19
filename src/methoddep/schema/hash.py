"""Stable ID / hash algorithm for method records.

The hash input is:
    customer "\\0" qualified_name "\\0" normalized_signature

and the output is the SHA-1 hex digest (40 lowercase hex chars).

The signature is normalized so that cosmetic differences (whitespace,
parameter names, default values, `const&` vs `const &`) do not change
the hash.
"""

from __future__ import annotations

import hashlib
import re

_WS_RE = re.compile(r"\s+")
_CONST_REF_RE = re.compile(r"const\s*&")
_CONST_PTR_RE = re.compile(r"const\s*\*")
_DEFAULT_ARG_RE = re.compile(r"\s*=\s*[^,)]+")
# Match a trailing identifier that follows a type token. Conservative:
# only strip when the identifier is preceded by an alnum or `&`/`*`/`]`/`>`
# and followed by `,` or `)` at any depth inside balanced brackets.
_PARAM_IDENT_RE = re.compile(
    r"(?P<pre>[\w&*\]>])\s+(?P<name>[A-Za-z_]\w*)\s*(?P<post>[,)])"
)


def _strip_param_names(sig: str) -> str:
    """Remove parameter names from a normalized signature.

    Applied repeatedly to handle multiple parameters in one pass; the
    regex intentionally avoids matching type tokens because `pre` requires
    a word/closing-symbol boundary.
    """
    prev = None
    cur = sig
    while prev != cur:
        prev = cur
        cur = _PARAM_IDENT_RE.sub(r"\g<pre>\g<post>", cur)
    return cur


def normalize_signature(signature: str) -> str:
    """Collapse whitespace, remove default args and parameter names,
    and canonicalize `const&`/`const*` spacing."""
    s = signature.strip()
    s = _DEFAULT_ARG_RE.sub("", s)
    s = _WS_RE.sub(" ", s)
    s = _CONST_REF_RE.sub("const&", s)
    s = _CONST_PTR_RE.sub("const*", s)
    s = _WS_RE.sub(" ", s).strip()
    s = _strip_param_names(s)
    # Tighten spaces around punctuation that is always unambiguous.
    s = re.sub(r"\s*,\s*", ", ", s)
    s = re.sub(r"\s*\(\s*", "(", s)
    s = re.sub(r"\s*\)\s*", ")", s)
    return s


def method_id(customer: str, qualified_name: str, signature: str) -> str:
    """Return the canonical 40-char hex SHA-1 id for a method record."""
    normalized = normalize_signature(signature)
    material = f"{customer}\x00{qualified_name}\x00{normalized}".encode("utf-8")
    return hashlib.sha1(material).hexdigest()
