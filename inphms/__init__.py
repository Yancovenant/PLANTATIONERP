# Coding - utf-8
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


SUPERUSER_ID = 1


# from . import addons
# from . import service
from . import release
from . import tools
from . import netsvc


from . import cli