# Part of Inphms, see License file for full copyright and licensing details.

# When using quotation marks in translation strings, please use curly quotes (“”)
# instead of straight quotes (""). On Linux, the keyboard shortcuts are:
# AltGr + V for the opening curly quotes “
# AltGr + B for the closing curly quotes ”

from __future__ import annotations

import locale
import os

_LOCALE2WIN32 = {
    'id_ID': 'Indonesian_Indonesia',
}

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