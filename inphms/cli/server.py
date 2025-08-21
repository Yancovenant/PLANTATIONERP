# Part of Inphms. See LICENSE file for full copyright and licensing details.

"""
INPHMS - Server
is an ERP Plantation Program.

The whole source code is distributed under the terms of the
GNU Public License

(c) 2025, Ian - INPHMS
"""

import atexit, csv, logging, os, re, sys

from pathlib import Path
from psycopg2.errors import InsufficientPrivilege

import inphms

from . import Command

__author__ = inphms.release.author
__version__ = inphms.release.version

_logger = logging.getLogger('inphms')

re._MAXCACHE = 4096

class Server(Command):
    """Start the inphms server (default command)"""
    def run(self, args):
        inphms.tools.config.parser.prog = f'{Path(sys.argv[0]).name} {self.name}'
        main(args)