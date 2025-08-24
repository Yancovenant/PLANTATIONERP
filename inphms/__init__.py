# -*- coding: utf-8 -*-
# Part of Inphms. See LICENSE file for full copyright and licensing details.

""" INPHMS core library. """

import pkgutil
import os.path
__path__ = [
    os.path.abspath(path)
    for path in pkgutil.extend_path(__path__, __name__)
]

import sys
MIN_PY_VERSION = (3, 10)
MAX_PY_VERSION = (3, 13)
assert sys.version_info > MIN_PY_VERSION, f"Outdated python version detected, Inphms requires Python >= {'.'.join(map(str,  MIN_PY_VERSION))} to run."

# ----------------------------------------------------------
# Shortcuts
# ----------------------------------------------------------
# The hard-coded super-user id (a.k.a. administrator, or root user).
SUPERUSER_ID = 1

# ----------------------------------------------------------
# Import tools to patch code and libraries
# required to do as early as possible for evented and timezone
# ----------------------------------------------------------
from . import _monkeypatches
_monkeypatches.patch_all()

# ----------------------------------------------------------
# Imports
# ----------------------------------------------------------
# from . import upgrade  # this namespace must be imported first
# from . import addons
from . import release
from . import netsvc
from . import modules
from . import addons
from . import service
from . import sql_db
from . import tools

## MODEL CLASSES
from . import api
from . import models

## OTHER IMPORT REQUIRED
from . import cli
from . import http

