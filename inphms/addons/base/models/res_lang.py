# -*- coding: utf-8 -*-
# Part of Inphms, see License file for full copyright and licensing details.

import ast
import json
import locale
import logging
import re
from typing import Any, Literal

from inphms import api, fields, models, tools, _
from inphms.exceptions import UserError, ValidationError
from inphms.tools import OrderedSet
from inphms.tools.misc import ReadonlyDict

_logger = logging.getLogger(__name__)

DEFAULT_DATE_FORMAT = '%m/%d/%Y'
DEFAULT_TIME_FORMAT = '%H:%M:%S'
DEFAULT_SHORT_TIME_FORMAT = '%H:%M'

class LangData(ReadonlyDict):
    """ A ``dict``-like class which can access field value like a ``res.lang`` record.
    Note: This data class cannot store data for fields with the same name as
    ``dict`` methods, like ``dict.keys``.
    """
    __slots__ = ()

    def __bool__(self) -> bool:
        return bool(self.id)

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError