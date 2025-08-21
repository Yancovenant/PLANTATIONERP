# Part of Inphms, see License file for full copyright and licensing details.

import configparser as configparser
import errno
import logging
import optparse
import glob
import os
import sys
import tempfile
import warnings

import inphms

from os.path import expandvars, expanduser, abspath, realpath, normcase
from .. import release, conf, loglevels
from . import appdirs

from passlib.context import CryptContext
crypt_context = CryptContext(schemes=['pbkdf2_sha512', 'plaintext'],
                             deprecated=['plaintext'],
                             pbkdf2_sha512__rounds=600_000)


class configmanager(object):
    def __init__(self, fname=None):
        """Constructor.

        :param fname: a shortcut allowing to instantiate :class:`configmanager`
                      from Python code without resorting to env variable
        """

        self.options = {
            'admin_pwd': 'supervisor',
            'root_path': None,
        }

        self.config_file = fname

        self._LOGLEVELS = dict([
            (getattr(loglevels, 'LOG_%s' % x), getattr(logging, x))
            for x in ('CRITICAL', 'ERROR', 'WARNING', 'INFO', 'DEBUG', 'NOTSET')
        ])

        version = "%s %s" % (release.description, release.version)

config = configmanager()