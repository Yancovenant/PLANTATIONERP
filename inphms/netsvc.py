# Part of Inphms, see License file for full copyright and licensing details.

import contextlib
import json
import logging
import logging.handlers
import os
import platform
import pprint
import sys
import threading
import time
import traceback
import warnings

import werkzeug.serving

from . import release
from . import tools

_logger = logging.getLogger(__name__)

showwarning = None
def init_logger():
    global showwarning
    if logging.getLogRecordFactory():
        return
    
    logging.setLogRecordFactory(LogRecord)

class LogRecord(logging.LogRecord):
    def __init__(self, name, level, pathname, lineno, msg, args, exc_info, func=None, sinfo=None):
        super().__init__(name, level, pathname, lineno, msg, args, exc_info, func, sinfo)
        self.perf_info = ""