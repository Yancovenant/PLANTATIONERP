# Part of Inphms, see License file for full copyright and licensing details.
"""
Miscellaneous tools used by Inphms.
"""
from __future__ import annotations

import base64
import collections
import csv
import datetime
import enum
import hashlib
import hmac as hmac_lib
import itertools
import json
import logging
import os
import re
import sys
import tempfile
import threading
import time
import traceback
import typing
import unicodedata
import warnings
import zlib

from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping, MutableMapping, MutableSet, Reversible
from contextlib import ContextDecorator, contextmanager
from difflib import HtmlDiff
from functools import reduce, wraps
from itertools import islice, groupby as itergroupby
from operator import itemgetter

import babel
import babel.dates
import markupsafe
import pytz
# from lxml import etree, objectify

import inphms
import inphms.addons

from .config import config

K = typing.TypeVar('K')
T = typing.TypeVar('T')
if typing.TYPE_CHECKING:
    from collections.abc import Callable, Collection, Sequence
    from inphms.api import Environment

    P = typing.TypeVar('P')

__all__ = [
    'file_path',
    'file_open',
    'reverse_enumerate',
    'frozendict',
    'unique',
    'DotDict',
    'consteq',
]

_logger = logging.getLogger(__name__)

# ----------------------------------------------------------
# File paths
# ----------------------------------------------------------

def file_path(file_path: str, filter_ext: tuple[str, ...] = ('',), env: Environment | None = None) -> str:
    """Verify that a file exists under a known `addons_path` directory and return its full path.

    Examples::

    >>> file_path('hr')
    >>> file_path('hr/static/description/icon.png')
    >>> file_path('hr/static/description/icon.png', filter_ext=('.png', '.jpg'))

    :param str file_path: absolute file path, or relative path within any `addons_path` directory
    :param list[str] filter_ext: optional list of supported extensions (lowercase, with leading dot)
    :param env: optional environment, required for a file path within a temporary directory
        created using `file_open_temporary_directory()`
    :return: the absolute path to the file
    :raise FileNotFoundError: if the file is not found under the known `addons_path` directories
    :raise ValueError: if the file doesn't have one of the supported extensions (`filter_ext`)
    """
    root_path = os.path.abspath(config['root_path'])
    temporary_paths = env.transaction._Transaction__file_open_tmp_paths if env else ()
    addons_paths = [*inphms.addons.__path__, root_path, *temporary_paths]
    is_abs = os.path.isabs(file_path)
    normalized_path = os.path.normpath(os.path.normcase(file_path))

    if filter_ext and not normalized_path.lower().endswith(filter_ext):
        raise ValueError("Unsupported file: " + file_path)

    # ignore leading 'addons/' if present, it's the final component of root_path, but
    # may sometimes be included in relative paths
    if normalized_path.startswith('addons' + os.sep):
        normalized_path = normalized_path[7:]

    for addons_dir in addons_paths:
        # final path sep required to avoid partial match
        parent_path = os.path.normpath(os.path.normcase(addons_dir)) + os.sep
        fpath = (normalized_path if is_abs else
                 os.path.normpath(os.path.normcase(os.path.join(parent_path, normalized_path))))
        if fpath.startswith(parent_path) and os.path.exists(fpath):
            return fpath

    raise FileNotFoundError("File not found: " + file_path)

def file_open(name: str, mode: str = "r", filter_ext: tuple[str, ...] = (), env: Environment | None = None):
    """Open a file from within the addons_path directories, as an absolute or relative path.

    Examples::

        >>> file_open('hr/static/description/icon.png')
        >>> file_open('hr/static/description/icon.png', filter_ext=('.png', '.jpg'))
        >>> with file_open('/opt/inphms/addons/hr/static/description/icon.png', 'rb') as f:
        ...     contents = f.read()

    :param name: absolute or relative path to a file located inside an addon
    :param mode: file open mode, as for `open()`
    :param list[str] filter_ext: optional list of supported extensions (lowercase, with leading dot)
    :param env: optional environment, required to open a file within a temporary directory
        created using `file_open_temporary_directory()`
    :return: file object, as returned by `open()`
    :raise FileNotFoundError: if the file is not found under the known `addons_path` directories
    :raise ValueError: if the file doesn't have one of the supported extensions (`filter_ext`)
    """
    path = file_path(name, filter_ext=filter_ext, env=env)
    if os.path.isfile(path):
        if 'b' not in mode:
            # Force encoding for text mode, as system locale could affect default encoding,
            # even with the latest Python 3 versions.
            # Note: This is not covered by a unit test, due to the platform dependency.
            #       For testing purposes you should be able to force a non-UTF8 encoding with:
            #         `sudo locale-gen fr_FR; LC_ALL=fr_FR.iso8859-1 python3 ...'
            # See also PEP-540, although we can't rely on that at the moment.
            return open(path, mode, encoding="utf-8")
        return open(path, mode)
    raise FileNotFoundError("Not a file: " + name)

#----------------------------------------------------------
# iterables
#----------------------------------------------------------
def reverse_enumerate(lst: Sequence[T]) -> Iterator[tuple[int, T]]: #ichecked
    """Like enumerate but in the other direction

    Usage::

        >>> a = ['a', 'b', 'c']
        >>> it = reverse_enumerate(a)
        >>> it.next()
        (2, 'c')
        >>> it.next()
        (1, 'b')
        >>> it.next()
        (0, 'a')
        >>> it.next()
        Traceback (most recent call last):
          File "<stdin>", line 1, in <module>
        StopIteration
    """
    return zip(range(len(lst) - 1, -1, -1), reversed(lst))

def scan_languages() -> list[tuple[str, str]]:
    """ Returns all languages supported by OpenERP for translation

    :returns: a list of (lang_code, lang_name) pairs
    :rtype: [(str, unicode)]
    """
    try:
        # read (code, name) from languages in base/data/res.lang.csv
        with file_open('base/data/res.lang.csv') as csvfile:
            reader = csv.reader(csvfile, delimiter=',', quotechar='"')
            fields = next(reader)
            code_index = fields.index("code")
            name_index = fields.index("name")
            result = [
                (row[code_index], row[name_index])
                for row in reader
            ]
    except Exception:
        _logger.error("Could not read res.lang.csv")
        result = []

    return sorted(result or [('en_US', u'English')], key=itemgetter(1))

# ---------------------------------------------
# String management
# ---------------------------------------------
def dumpstacks(sig=None, frame=None, thread_idents=None, log_level=logging.INFO):
    """ Signal handler: dump a stack trace for each existing thread or given
    thread(s) specified through the ``thread_idents`` sequence.
    """
    code = []

    def extract_stack(stack):
        for filename, lineno, name, line in traceback.extract_stack(stack):
            yield 'File: "%s", line %d, in %s' % (filename, lineno, name)
            if line:
                yield "  %s" % (line.strip(),)

    # code from http://stackoverflow.com/questions/132058/getting-stack-trace-from-a-running-python-application#answer-2569696
    # modified for python 2.5 compatibility
    threads_info = {th.ident: {'repr': repr(th),
                               'uid': getattr(th, 'uid', 'n/a'),
                               'dbname': getattr(th, 'dbname', 'n/a'),
                               'url': getattr(th, 'url', 'n/a'),
                               'query_count': getattr(th, 'query_count', 'n/a'),
                               'query_time': getattr(th, 'query_time', None),
                               'perf_t0': getattr(th, 'perf_t0', None)}
                    for th in threading.enumerate()}
    for threadId, stack in sys._current_frames().items():
        if not thread_idents or threadId in thread_idents:
            thread_info = threads_info.get(threadId, {})
            query_time = thread_info.get('query_time')
            perf_t0 = thread_info.get('perf_t0')
            remaining_time = None
            if query_time is not None and perf_t0:
                remaining_time = '%.3f' % (time.time() - perf_t0 - query_time)
                query_time = '%.3f' % query_time
            # qc:query_count qt:query_time pt:python_time (aka remaining time)
            code.append("\n# Thread: %s (db:%s) (uid:%s) (url:%s) (qc:%s qt:%s pt:%s)" %
                        (thread_info.get('repr', threadId),
                         thread_info.get('dbname', 'n/a'),
                         thread_info.get('uid', 'n/a'),
                         thread_info.get('url', 'n/a'),
                         thread_info.get('query_count', 'n/a'),
                         query_time or 'n/a',
                         remaining_time or 'n/a'))
            for line in extract_stack(stack):
                code.append(line)

    if inphms.evented:
        # code from http://stackoverflow.com/questions/12510648/in-gevent-how-can-i-dump-stack-traces-of-all-running-greenlets
        import gc
        from greenlet import greenlet
        for ob in gc.get_objects():
            if not isinstance(ob, greenlet) or not ob:
                continue
            code.append("\n# Greenlet: %r" % (ob,))
            for line in extract_stack(ob.gr_frame):
                code.append(line)

    _logger.log(log_level, "\n".join(code))

class Callbacks:
    """ A simple queue of callback functions.  Upon run, every function is
    called (in addition order), and the queue is emptied.

    ::

        callbacks = Callbacks()

        # add foo
        def foo():
            print("foo")

        callbacks.add(foo)

        # add bar
        callbacks.add
        def bar():
            print("bar")

        # add foo again
        callbacks.add(foo)

        # call foo(), bar(), foo(), then clear the callback queue
        callbacks.run()

    The queue also provides a ``data`` dictionary, that may be freely used to
    store anything, but is mostly aimed at aggregating data for callbacks.  The
    dictionary is automatically cleared by ``run()`` once all callback functions
    have been called.

    ::

        # register foo to process aggregated data
        @callbacks.add
        def foo():
            print(sum(callbacks.data['foo']))

        callbacks.data.setdefault('foo', []).append(1)
        ...
        callbacks.data.setdefault('foo', []).append(2)
        ...
        callbacks.data.setdefault('foo', []).append(3)

        # call foo(), which prints 6
        callbacks.run()

    Given the global nature of ``data``, the keys should identify in a unique
    way the data being stored.  It is recommended to use strings with a
    structure like ``"{module}.{feature}"``.
    """
    __slots__ = ['_funcs', 'data']

    def __init__(self):
        self._funcs: collections.deque[Callable] = collections.deque()
        self.data = {}
    
    def add(self, func: Callable) -> None:
        """ Add the given function. """
        self._funcs.append(func)
    
    def run(self) -> None:
        """ Call all the functions (in addition order), then clear associated data.
        """
        while self._funcs:
            func = self._funcs.popleft()
            func()
        self.clear()

    def clear(self) -> None:
        """ Remove all callbacks and data from self. """
        self._funcs.clear()
        self.data.clear()

def unique(it: Iterable[T]) -> Iterator[T]: #ichecked
    """ "Uniquifier" for the provided iterable: will output each element of
    the iterable once.

    The iterable's elements must be hashahble.

    :param Iterable it:
    :rtype: Iterator
    """
    seen = set()
    for e in it:
        if e not in seen:
            seen.add(e)
            yield e

def submap(mapping: Mapping[K, T], keys: Iterable[K]) -> Mapping[K, T]: #ichecked
    """
    Get a filtered copy of the mapping where only some keys are present.

    :param Mapping mapping: the original dict-like structure to filter
    :param Iterable keys: the list of keys to keep
    :return dict: a filtered dict copy of the original mapping
    """
    keys = frozenset(keys)
    return {key: mapping[key] for key in mapping if key in keys}

class frozendict(dict[K, T], typing.Generic[K, T]):
    """ An implementation of an immutable dictionary. """
    __slots__ = ()

    def __delitem__(self, key):
        raise NotImplementedError("'__delitem__' not supported on frozendict")

    def __setitem__(self, key, val):
        raise NotImplementedError("'__setitem__' not supported on frozendict")

    def clear(self):
        raise NotImplementedError("'clear' not supported on frozendict")

    def pop(self, key, default=None):
        raise NotImplementedError("'pop' not supported on frozendict")

    def popitem(self):
        raise NotImplementedError("'popitem' not supported on frozendict")

    def setdefault(self, key, default=None):
        raise NotImplementedError("'setdefault' not supported on frozendict")

    def update(self, *args, **kwargs):
        raise NotImplementedError("'update' not supported on frozendict")

    def __hash__(self) -> int:  # type: ignore
        return hash(frozenset((key, freehash(val)) for key, val in self.items()))

class ReadonlyDict(Mapping[K, T], typing.Generic[K, T]):
    """Helper for an unmodifiable dictionary, not even updatable using `dict.update`.

    This is similar to a `frozendict`, with one drawback and one advantage:

    - `dict.update` works for a `frozendict` but not for a `ReadonlyDict`.
    - `json.dumps` works for a `frozendict` by default but not for a `ReadonlyDict`.

    This comes from the fact `frozendict` inherits from `dict`
    while `ReadonlyDict` inherits from `collections.abc.Mapping`.

    So, depending on your needs,
    whether you absolutely must prevent the dictionary from being updated (e.g., for security reasons)
    or you require it to be supported by `json.dumps`, you can choose either option.

        E.g.
          data = ReadonlyDict({'foo': 'bar'})
          data['baz'] = 'xyz' # raises exception
          data.update({'baz', 'xyz'}) # raises exception
          dict.update(data, {'baz': 'xyz'}) # raises exception
    """
    def __init__(self, data):
        self.__data = dict(data)

    def __contains__(self, key: K):
        return key in self.__data

    def __getitem__(self, key: K) -> T:
        try:
            return self.__data[key]
        except KeyError:
            if hasattr(type(self), "__missing__"):
                return self.__missing__(key)
            raise

    def __len__(self):
        return len(self.__data)

    def __iter__(self):
        return iter(self.__data)

class DotDict(dict):
    """Helper for dot.notation access to dictionary attributes

        E.g.
          foo = DotDict({'bar': False})
          return foo.bar
    """
    def __getattr__(self, attrib):
        val = self.get(attrib)
        return DotDict(val) if isinstance(val, dict) else val

consteq = hmac_lib.compare_digest