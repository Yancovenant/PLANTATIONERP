# Part of Inphms, see License file for full copyright and licensing details.

import ast
import collections.abc
import copy
import functools
import importlib
import importlib.metadata
import logging
import os
import re
import sys
import traceback
import warnings
from os.path import join as opj, normpath

import inphms
import inphms.tools as tools
import inphms.release as release

_logger = logging.getLogger(__name__)

def initialize_sys_path():
    """
    Setup the addons path ``inphms.addons.__path__`` with various defaults
    and explicit directories.
    """
    dd = os.path.normcase(tools.config.addons_data_dir)
    if os.access(dd, os.R_OK) and dd not in inphms.addons.__path__:
        inphms.addons.__path__.append(dd)