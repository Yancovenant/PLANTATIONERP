# Part of Inphms, see License file for full copyright and licensing details.

# When using quotation marks in translation strings, please use curly quotes (“”)
# instead of straight quotes (""). On Linux, the keyboard shortcuts are:
# AltGr + V for the opening curly quotes “
# AltGr + B for the closing curly quotes ”

from __future__ import annotations

import codecs
import fnmatch
import functools
import inspect
import io
import itertools
import json
import locale
import logging
import os
# import polib
import re
import tarfile
import typing
import warnings
from collections import defaultdict, namedtuple
from contextlib import suppress
from datetime import datetime
from os.path import join
from pathlib import Path
from tokenize import generate_tokens, STRING, NEWLINE, INDENT, DEDENT

from babel.messages import extract
# from lxml import etree, html
from markupsafe import escape, Markup
from psycopg2.extras import Json

import inphms
from inphms.exceptions import UserError
from .config import config
# from .misc import file_open, file_path, get_iso_codes, OrderedSet, ReadonlyDict, SKIPPED_ELEMENT_TYPES

# __all__ = [
#     "_",
#     "LazyTranslate",
#     "html_translate",
#     "xml_translate",
# ]

_logger = logging.getLogger(__name__)

PYTHON_TRANSLATION_COMMENT = 'inphms-python'

# translation used for javascript code in web client
JAVASCRIPT_TRANSLATION_COMMENT = 'inphms-javascript'

SKIPPED_ELEMENTS = ('script', 'style', 'title')

_LOCALE2WIN32 = {
    'id_ID': 'Indonesian_Indonesia',
}

# these direct uses of CSV are ok.
import csv # pylint: disable=deprecated-module
class UNIX_LINE_TERMINATOR(csv.excel):
    lineterminator = '\n'

csv.register_dialect("UNIX", UNIX_LINE_TERMINATOR)

# which elements are translated inline
TRANSLATED_ELEMENTS = {
    'abbr', 'b', 'bdi', 'bdo', 'br', 'cite', 'code', 'data', 'del', 'dfn', 'em',
    'font', 'i', 'ins', 'kbd', 'keygen', 'mark', 'math', 'meter', 'output',
    'progress', 'q', 'ruby', 's', 'samp', 'small', 'span', 'strong', 'sub',
    'sup', 'time', 'u', 'var', 'wbr', 'text', 'select', 'option',
}

# Which attributes must be translated. This is a dict, where the value indicates
# a condition for a node to have the attribute translatable.
# ⚠ Note that it implicitly includes their t-attf-* equivalent.
TRANSLATED_ATTRS = dict.fromkeys({
    'string', 'add-label', 'help', 'sum', 'avg', 'confirm', 'placeholder', 'alt', 'title', 'aria-label',
    'aria-keyshortcuts', 'aria-placeholder', 'aria-roledescription', 'aria-valuetext',
    'value_label', 'data-tooltip', 'label', 'cancel-label', 'confirm-label',
}, lambda e: True)

def translate_attrib_value(node):
    # check if the value attribute of a node must be translated
    classes = node.attrib.get('class', '').split(' ')
    return (
        (node.tag == 'input' and node.attrib.get('type', 'text') == 'text')
        and 'datetimepicker-input' not in classes
        or (node.tag == 'input' and node.attrib.get('type') == 'hidden')
        and 'inphms_translatable_input_hidden' in classes
    )

TRANSLATED_ATTRS.update(
    value=translate_attrib_value,
    text=lambda e: (e.tag == 'field' and e.attrib.get('widget', '') == 'url'),
    **{f't-attf-{attr}': cond for attr, cond in TRANSLATED_ATTRS.items()},
)

# This should match the list provided to INL (New Inphms Library) (see translatableAttributes).
NIL_TRANSLATED_ATTRS = {
    "alt",
    "aria-label",
    "aria-placeholder",
    "aria-roledescription",
    "aria-valuetext",
    "data-tooltip",
    "label",
    "placeholder",
    "title",
}

avoid_pattern = re.compile(r"\s*<!DOCTYPE", re.IGNORECASE | re.MULTILINE | re.UNICODE)
space_pattern = re.compile(r"[\s\uFEFF]*")  # web_editor uses \uFEFF as ZWNBSP


def get_text_alias(source: str, *args, **kwargs):
    assert not (args and kwargs)
    assert isinstance(source, str)
    module, lang = _get_translation_source(1)
    return get_translation(module, lang, source, args or kwargs)

@functools.total_ordering
class LazyGettext:
    """ Lazy code translated term.

    Similar to get_text_alias but the translation lookup will be done only at
    __str__ execution.
    This eases the search for terms to translate as lazy evaluated strings
    are declared early.

    A code using translated global variables such as:

    ```
    _lt = LazyTranslate(__name__)
    LABEL = _lt("User")

    def _compute_label(self):
        env = self.with_env(lang=self.partner_id.lang).env
        self.user_label = env._(LABEL)
    ```

    works as expected (unlike the classic get_text_alias implementation).
    """

    __slots__ = ('_args', '_default_lang', '_module', '_source')

    def __init__(self, source, *args, _module='', _default_lang='', **kwargs):
        assert not (args and kwargs)
        assert isinstance(source, str)
        self._source = source
        self._args = args or kwargs
        self._module = get_translated_module(_module or 2)
        self._default_lang = _default_lang

    def _translate(self, lang: str = '') -> str:
        module, lang = _get_translation_source(2, self._module, lang, default_lang=self._default_lang)
        return get_translation(module, lang, self._source, self._args)

    def __repr__(self):
        """ Show for the debugger"""
        args = {'_module': self._module, '_default_lang': self._default_lang, '_args': self._args}
        return f"_lt({self._source!r}, **{args!r})"

    def __str__(self):
        """ Translate."""
        return self._translate()

    def __eq__(self, other):
        """ Prevent using equal operators

        Prevent direct comparisons with ``self``.
        One should compare the translation of ``self._source`` as ``str(self) == X``.
        """
        raise NotImplementedError()

    def __hash__(self):
        raise NotImplementedError()

    def __lt__(self, other):
        raise NotImplementedError()

    def __add__(self, other):
        if isinstance(other, str):
            return self._translate() + other
        elif isinstance(other, LazyGettext):
            return self._translate() + other._translate()
        return NotImplemented

    def __radd__(self, other):
        if isinstance(other, str):
            return other + self._translate()
        return NotImplemented

_ = get_text_alias
_lt = LazyGettext



def get_locales(lang=None):
    if lang is None:
        lang = locale.getlocale()[0]

    if os.name == 'nt':
        lang = _LOCALE2WIN32.get(lang, lang)

    def process(enc):
        ln = locale._build_localename((lang, enc))
        yield ln
        nln = locale.normalize(ln)
        if nln != ln:
            yield nln

    for x in process('utf8'): yield x

    prefenc = locale.getpreferredencoding()
    if prefenc:
        for x in process(prefenc): yield x

        prefenc = {
            'latin1': 'latin9',
            'iso-8859-1': 'iso8859-15',
            'cp1252': '1252',
        }.get(prefenc.lower())
        if prefenc:
            for x in process(prefenc): yield x

    yield lang

def resetlocale():
    # locale.resetlocale is bugged with some locales.
    for ln in get_locales():
        try:
            return locale.setlocale(locale.LC_ALL, ln)
        except locale.Error:
            continue

# xml_translate.get_text_content = get_text_content
# html_translate.get_text_content = get_text_content

# xml_translate.term_converter = xml_term_converter
# html_translate.term_converter = html_term_converter

# xml_translate.is_text = is_text
# html_translate.is_text = is_text

# xml_translate.term_adapter = xml_term_adapter

def translate_sql_constraint(cr, key, lang):
    cr.execute("""
        SELECT COALESCE(c.message->>%s, c.message->>'en_US') as message
        FROM ir_model_constraint c
        WHERE name=%s and type='u'
        """, (lang, key))
    return cr.fetchone()[0]