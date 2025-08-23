# ruff: noqa: PLC0415 (import in function not at top-level)

from __future__ import annotations

import contextlib
import operator
import os
import re
import sys
import typing as t
import warnings
from shutil import copyfileobj
from types import CodeType

from werkzeug import urls
from werkzeug.datastructures import FileStorage, MultiDict
from werkzeug.routing import Rule
from werkzeug.urls import _decode_idna
from werkzeug.wrappers import Request, Response

Rule_get_func_code = hasattr(Rule, '_get_func_code') and Rule._get_func_code

_default_encoding = sys.getdefaultencoding()



if t.TYPE_CHECKING:
    from werkzeug import datastructures as ds

# A regular expression for what a valid schema looks like
_scheme_re = re.compile(r"^[a-zA-Z0-9+-.]+$")

# Characters that are safe in any part of an URL.
_always_safe_chars = (
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789"
    "-._~"
    "$!'()*+,;"  # RFC3986 sub-delims set, not including query string delimiters &=
)
_always_safe = frozenset(_always_safe_chars.encode("ascii"))

_hexdigits = "0123456789ABCDEFabcdef"
_hextobyte = {
    f"{a}{b}".encode("ascii"): int(f"{a}{b}", 16)
    for a in _hexdigits
    for b in _hexdigits
}
_bytetohex = [f"%{char:02X}".encode("ascii") for char in range(256)]

def patch_werkzeug():
    from ..tools.json import scriptsafe  # noqa: PLC0415
    Request.json_module = Response.json_module = scriptsafe

    FileStorage.save = lambda self, dst, buffer_size=(1 << 20): copyfileobj(self.stream, dst, buffer_size)

    def _multidict_deepcopy(self, memo=None):
        return orig_deepcopy(self)

    orig_deepcopy = MultiDict.deepcopy
    MultiDict.deepcopy = _multidict_deepcopy

    if Rule_get_func_code:
        @staticmethod
        def _get_func_code(code, name):
            assert isinstance(code, CodeType)
            return Rule_get_func_code(code, name)
        Rule._get_func_code = _get_func_code

    if hasattr(urls, 'url_join'):
        # URLs are already patched
        return
    # see https://github.com/pallets/werkzeug/compare/2.3.0..3.0.0
    # see https://github.com/pallets/werkzeug/blob/2.3.0/src/werkzeug/urls.py for replacement
    urls.url_decode = url_decode
    urls.url_encode = url_encode
    urls.url_join = url_join
    urls.url_parse = url_parse
    urls.url_quote = url_quote
    urls.url_unquote = url_unquote
    urls.url_quote_plus = url_quote_plus
    urls.url_unquote_plus = url_unquote_plus
    urls.url_unparse = url_unparse
    urls.URL = URL

class _URLTuple(t.NamedTuple):
    scheme: str
    netloc: str
    path: str
    query: str
    fragment: str

class BaseURL(_URLTuple):
    """Superclass of :py:class:`URL` and :py:class:`BytesURL`.

    .. deprecated:: 2.3
        Will be removed in Werkzeug 2.4. Use the ``urllib.parse`` library instead.
    """

    __slots__ = ()
    _at: str
    _colon: str
    _lbracket: str
    _rbracket: str

class URL(BaseURL):
    """Represents a parsed URL.  This behaves like a regular tuple but
    also has some extra attributes that give further insight into the
    URL.

    .. deprecated:: 2.3
        Will be removed in Werkzeug 2.4. Use the ``urllib.parse`` library instead.
    """

    __slots__ = ()
    _at = "@"
    _colon = ":"
    _lbracket = "["
    _rbracket = "]"

    def encode(self, charset: str = "utf-8", errors: str = "replace") -> BytesURL:
        """Encodes the URL to a tuple made out of bytes.  The charset is
        only being used for the path, query and fragment.
        """
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", "'werkzeug", DeprecationWarning)
            return BytesURL(
                self.scheme.encode("ascii"),
                self.encode_netloc(),
                self.path.encode(charset, errors),
                self.query.encode(charset, errors),
                self.fragment.encode(charset, errors),
            )

def url_unparse(components: tuple[str, str, str, str, str]) -> str:
    """The reverse operation to :meth:`url_parse`.  This accepts arbitrary
    as well as :class:`URL` tuples and returns a URL as a string.

    :param components: the parsed URL as tuple which should be converted
                       into a URL string.

    .. deprecated:: 2.3
        Will be removed in Werkzeug 2.4. Use ``urllib.parse.urlunsplit`` instead.
    """

    _check_str_tuple(components)
    scheme, netloc, path, query, fragment = components
    s = _make_encode_wrapper(scheme)
    url = s("")

    # We generally treat file:///x and file:/x the same which is also
    # what browsers seem to do.  This also allows us to ignore a schema
    # register for netloc utilization or having to differentiate between
    # empty and missing netloc.
    if netloc or (scheme and path.startswith(s("/"))):
        if path and path[:1] != s("/"):
            path = s("/") + path
        url = s("//") + (netloc or s("")) + path
    elif path:
        url += path
    if scheme:
        url = scheme + s(":") + url
    if query:
        url = url + s("?") + query
    if fragment:
        url = url + s("#") + fragment
    return url

def url_unquote_plus(
    s: str | bytes, charset: str = "utf-8", errors: str = "replace"
) -> str:
    """URL decode a single string with the given `charset` and decode "+" to
    whitespace.

    Per default encoding errors are ignored.  If you want a different behavior
    you can set `errors` to ``'replace'`` or ``'strict'``.

    :param s: The string to unquote.
    :param charset: the charset of the query string.  If set to `None`
        no decoding will take place.
    :param errors: The error handling for the `charset` decoding.

    .. deprecated:: 2.3
        Will be removed in Werkzeug 2.4. Use ``urllib.parse.unquote_plus`` instead.
    """
    if isinstance(s, str):
        s = s.replace("+", " ")
    else:
        s = s.replace(b"+", b" ")

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", "'werkzeug", DeprecationWarning)
        return url_unquote(s, charset, errors)

def url_quote_plus(
    string: str, charset: str = "utf-8", errors: str = "strict", safe: str = ""
) -> str:
    """URL encode a single string with the given encoding and convert
    whitespace to "+".

    :param s: The string to quote.
    :param charset: The charset to be used.
    :param safe: An optional sequence of safe characters.

    .. deprecated:: 2.3
        Will be removed in Werkzeug 2.4. Use ``urllib.parse.quote_plus`` instead.
    """

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", "'werkzeug", DeprecationWarning)
        return url_quote(string, charset, errors, safe + " ", "+").replace(" ", "+")

def url_unquote(
    s: str | bytes,
    charset: str = "utf-8",
    errors: str = "replace",
    unsafe: str = "",
) -> str:
    """URL decode a single string with a given encoding.  If the charset
    is set to `None` no decoding is performed and raw bytes are
    returned.

    :param s: the string to unquote.
    :param charset: the charset of the query string.  If set to `None`
        no decoding will take place.
    :param errors: the error handling for the charset decoding.

    .. deprecated:: 2.3
        Will be removed in Werkzeug 2.4. Use ``urllib.parse.unquote`` instead.
    """
    rv = _unquote_to_bytes(s, unsafe)
    if charset is None:
        return rv
    return rv.decode(charset, errors)

def url_quote(
    string: str | bytes,
    charset: str = "utf-8",
    errors: str = "strict",
    safe: str | bytes = "/:",
    unsafe: str | bytes = "",
) -> str:
    """URL encode a single string with a given encoding.

    :param s: the string to quote.
    :param charset: the charset to be used.
    :param safe: an optional sequence of safe characters.
    :param unsafe: an optional sequence of unsafe characters.

    .. deprecated:: 2.3
        Will be removed in Werkzeug 2.4. Use ``urllib.parse.quote`` instead.

    .. versionadded:: 0.9.2
       The `unsafe` parameter was added.
    """

    if not isinstance(string, (str, bytes, bytearray)):
        string = str(string)
    if isinstance(string, str):
        string = string.encode(charset, errors)
    if isinstance(safe, str):
        safe = safe.encode(charset, errors)
    if isinstance(unsafe, str):
        unsafe = unsafe.encode(charset, errors)
    safe = (frozenset(bytearray(safe)) | _always_safe) - frozenset(bytearray(unsafe))
    rv = bytearray()
    for char in bytearray(string):
        if char in safe:
            rv.append(char)
        else:
            rv.extend(_bytetohex[char])
    return bytes(rv).decode(charset)

def url_parse(
    url: str, scheme: str | None = None, allow_fragments: bool = True
) -> BaseURL:
    """Parses a URL from a string into a :class:`URL` tuple.  If the URL
    is lacking a scheme it can be provided as second argument. Otherwise,
    it is ignored.  Optionally fragments can be stripped from the URL
    by setting `allow_fragments` to `False`.

    The inverse of this function is :func:`url_unparse`.

    :param url: the URL to parse.
    :param scheme: the default schema to use if the URL is schemaless.
    :param allow_fragments: if set to `False` a fragment will be removed
                            from the URL.

    .. deprecated:: 2.3
        Will be removed in Werkzeug 2.4. Use ``urllib.parse.urlsplit`` instead.
    """
    s = _make_encode_wrapper(url)
    is_text_based = isinstance(url, str)

    if scheme is None:
        scheme = s("")
    netloc = query = fragment = s("")
    i = url.find(s(":"))
    if i > 0 and _scheme_re.match(_to_str(url[:i], errors="replace")):
        # make sure "iri" is not actually a port number (in which case
        # "scheme" is really part of the path)
        rest = url[i + 1 :]
        if not rest or any(c not in s("0123456789") for c in rest):
            # not a port number
            scheme, url = url[:i].lower(), rest

    if url[:2] == s("//"):
        delim = len(url)
        for c in s("/?#"):
            wdelim = url.find(c, 2)
            if wdelim >= 0:
                delim = min(delim, wdelim)
        netloc, url = url[2:delim], url[delim:]
        if (s("[") in netloc and s("]") not in netloc) or (
            s("]") in netloc and s("[") not in netloc
        ):
            raise ValueError("Invalid IPv6 URL")

    if allow_fragments and s("#") in url:
        url, fragment = url.split(s("#"), 1)
    if s("?") in url:
        url, query = url.split(s("?"), 1)

    result_type = URL if is_text_based else BytesURL

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", "'werkzeug", DeprecationWarning)
        return result_type(scheme, netloc, url, query, fragment)

def url_join(
    base: str | tuple[str, str, str, str, str],
    url: str | tuple[str, str, str, str, str],
    allow_fragments: bool = True,
) -> str:
    """Join a base URL and a possibly relative URL to form an absolute
    interpretation of the latter.

    :param base: the base URL for the join operation.
    :param url: the URL to join.
    :param allow_fragments: indicates whether fragments should be allowed.

    .. deprecated:: 2.3
        Will be removed in Werkzeug 2.4. Use ``urllib.parse.urljoin`` instead.
    """
    if isinstance(base, tuple):
        base = url_unparse(base)
    if isinstance(url, tuple):
        url = url_unparse(url)

    _check_str_tuple((base, url))
    s = _make_encode_wrapper(base)

    if not base:
        return url
    if not url:
        return base

    bscheme, bnetloc, bpath, bquery, _bfragment = url_parse(
        base, allow_fragments=allow_fragments
    )
    scheme, netloc, path, query, fragment = url_parse(url, bscheme, allow_fragments)
    if scheme != bscheme:
        return url
    if netloc:
        return url_unparse((scheme, netloc, path, query, fragment))
    netloc = bnetloc

    if path[:1] == s("/"):
        segments = path.split(s("/"))
    elif not path:
        segments = bpath.split(s("/"))
        if not query:
            query = bquery
    else:
        segments = bpath.split(s("/"))[:-1] + path.split(s("/"))

    # If the rightmost part is "./" we want to keep the slash but
    # remove the dot.
    if segments[-1] == s("."):
        segments[-1] = s("")

    # Resolve ".." and "."
    segments = [segment for segment in segments if segment != s(".")]
    while True:
        i = 1
        n = len(segments) - 1
        while i < n:
            if segments[i] == s("..") and segments[i - 1] not in (s(""), s("..")):
                del segments[i - 1 : i + 1]
                break
            i += 1
        else:
            break

    # Remove trailing ".." if the URL is absolute
    unwanted_marker = [s(""), s("..")]
    while segments[:2] == unwanted_marker:
        del segments[1]

    path = s("/").join(segments)
    return url_unparse((scheme, netloc, path, query, fragment))

def url_encode(
    obj: t.Mapping[str, str] | t.Iterable[tuple[str, str]],
    charset: str = "utf-8",
    sort: bool = False,
    key: t.Callable[[tuple[str, str]], t.Any] | None = None,
    separator: str = "&",
) -> str:
    """URL encode a dict/`MultiDict`.  If a value is `None` it will not appear
    in the result string.  Per default only values are encoded into the target
    charset strings.

    :param obj: the object to encode into a query string.
    :param charset: the charset of the query string.
    :param sort: set to `True` if you want parameters to be sorted by `key`.
    :param separator: the separator to be used for the pairs.
    :param key: an optional function to be used for sorting.  For more details
                check out the :func:`sorted` documentation.

    .. deprecated:: 2.3
        Will be removed in Werkzeug 2.4. Use ``urllib.parse.urlencode`` instead.

    .. versionchanged:: 2.1
        The ``encode_keys`` parameter was removed.

    .. versionchanged:: 0.5
        Added the ``sort``, ``key``, and ``separator`` parameters.
    """
    separator = _to_str(separator, "ascii")
    return separator.join(_url_encode_impl(obj, charset, sort, key))

def url_decode(
    s: t.AnyStr,
    charset: str = "utf-8",
    include_empty: bool = True,
    errors: str = "replace",
    separator: str = "&",
    cls: type[ds.MultiDict] | None = None,
) -> ds.MultiDict[str, str]:
    """Parse a query string and return it as a :class:`MultiDict`.

    :param s: The query string to parse.
    :param charset: Decode bytes to string with this charset. If not
        given, bytes are returned as-is.
    :param include_empty: Include keys with empty values in the dict.
    :param errors: Error handling behavior when decoding bytes.
    :param separator: Separator character between pairs.
    :param cls: Container to hold result instead of :class:`MultiDict`.

    .. deprecated:: 2.3
        Will be removed in Werkzeug 2.4. Use ``urllib.parse.parse_qs`` instead.

    .. versionchanged:: 2.1
        The ``decode_keys`` parameter was removed.

    .. versionchanged:: 0.5
        In previous versions ";" and "&" could be used for url decoding.
        Now only "&" is supported. If you want to use ";", a different
        ``separator`` can be provided.

    .. versionchanged:: 0.5
        The ``cls`` parameter was added.
    """
    if cls is None:
        from werkzeug.datastructures import MultiDict  # noqa: F811

        cls = MultiDict
    if isinstance(s, str) and not isinstance(separator, str):
        separator = separator.decode(charset or "ascii")
    elif isinstance(s, bytes) and not isinstance(separator, bytes):
        separator = separator.encode(charset or "ascii")  # type: ignore
    return cls(
        _url_decode_impl(
            s.split(separator), charset, include_empty, errors  # type: ignore
        )
    )