# Part of Inphms, see License file for full copyright and licensing details.

"""The Inphms API module defines Inphms Environments and method decorators.
"""

__all__ = [
    'Environment',
    'Meta',
    'model',
    'constrains', 'depends', 'onchange', 'returns',
    'call_kw',
]

import logging
import warnings
from collections import defaultdict
from collections.abc import Mapping
from contextlib import contextmanager
from inspect import signature
from pprint import pformat
from weakref import WeakSet

try:
    from decorator import decoratorx as decorator
except ImportError:
    from decorator import decorator


_logger = logging.getLogger(__name__)

class Environment(Mapping):
    """ The environment stores various contextual data used by the ORM:

    - :attr:`cr`: the current database cursor (for database queries);
    - :attr:`uid`: the current user id (for access rights checks);
    - :attr:`context`: the current context dictionary (arbitrary metadata);
    - :attr:`su`: whether in superuser mode.

    It provides access to the registry by implementing a mapping from model
    names to models. It also holds a cache for records, and a data
    structure to manage recomputations.
    """
    # cr: BaseCursor
    # uid: int
    # context: frozendict
    # su: bool
    # registry: Registry
    # cache: Cache
    # transaction: Transaction

    def reset(self):
        """ Reset the transaction, see :meth:`Transaction.reset`. """
        self.transaction.reset()

class Meta(type):
    """ Metaclass that automatically decorates traditional-style methods by
        guessing their API. It also implements the inheritance of the
        :func:`returns` decorators.
    """
    