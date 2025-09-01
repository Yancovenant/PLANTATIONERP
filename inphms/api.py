# Part of Inphms, see License file for full copyright and licensing details.

"""The Inphms API module defines Inphms Environments and method decorators.
"""
from __future__ import annotations

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

# from .exceptions import AccessError, UserError, CacheMiss
# from .tools import clean_context, frozendict, lazy_property, OrderedSet, Query, SQL
# from .tools.translate import get_translation, get_translated_module, LazyGettext
# from inphms.tools.misc import StackMap

import typing
if typing.TYPE_CHECKING:
    from collections.abc import Callable
    from inphms.sql_db import BaseCursor
    from inphms.models import BaseModel
    try:
        from typing_extensions import Self  # noqa: F401
    except ImportError:
        from typing import Self  # noqa: F401
    M = typing.TypeVar("M", bound=BaseModel)
else:
    Self = None
    M = typing.TypeVar("M")

DomainType = list[str | tuple[str, str, typing.Any]]
ContextType = Mapping[str, typing.Any]
ValuesType = dict[str, typing.Any]
T = typing.TypeVar('T')

_logger = logging.getLogger(__name__)

MAX_FIXPOINT_ITERATIONS = 10

# DONE
class NewId:
    """ Pseudo-ids for new records, encapsulating an optional origin id (actual
        record id) and an optional reference (any value).
    """
    __slots__ = ['origin', 'ref']

    def __init__(self, origin=None, ref=None):
        self.origin = origin
        self.ref = ref
    
    def __bool__(self):
        return False

    def __eq__(self, other):
        return isinstance(other, NewId) and (
            (self.origin and other.origin and self.origin == other.origin)
            or (self.ref and other.ref and self.ref == other.ref)
        )

    def __hash__(self):
        return hash(self.origin or self.ref or id(self))

    def __repr__(self):
        return (
            "<NewId origin=%r>" % self.origin if self.origin else
            "<NewId ref=%r>" % self.ref if self.ref else
            "<NewId 0x%x>" % id(self)
        )

    def __str__(self):
        if self.origin or self.ref:
            id_part = repr(self.origin or self.ref)
        else:
            id_part = hex(id(self))
        return "NewId_%s" % id_part

IdType: typing.TypeAlias = int | NewId

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

def propagate(method1, method2):
    """ Propagate decorators from ``method1`` to ``method2``, and return the
        resulting method.
    """
    if method1:
        for attr in ('_returns',):
            if hasattr(method1, attr) and not hasattr(method2, attr):
                setattr(method2, attr, getattr(method1, attr))
    return method2

class Meta(type):
    """ Metaclass that automatically decorates traditional-style methods by
        guessing their API. It also implements the inheritance of the
        :func:`returns` decorators.
    """
    def __new__(meta, name, bases, attrs):
        # dummy parent class to catch overridden methods decorated with 'returns'
        parent = type.__new__(meta, name, bases, {})

        for key, value in list(attrs.items()):
            if not key.startswith('__') and callable(value):
                # make the method inherit from decorators
                value = propagate(getattr(parent, key, None), value)
                attrs[key] = value

        return type.__new__(meta, name, bases, attrs)


_create_logger = logging.getLogger(__name__ + '.create')

@decorator
def _model_create_single(create, self, arg):
    # 'create' expects a dict and returns a record
    if isinstance(arg, Mapping):
        return create(self, arg)
    if len(arg) > 1:
        _create_logger.debug("%s.create() called with %d dicts", self, len(arg))
    return self.browse().concat(*(create(self, vals) for vals in arg))

def model_create_single(method: T) -> T:
    """ Decorate a method that takes a dictionary and creates a single record.
        The method may be called with either a single dict or a list of dicts::

            record = model.create(vals)
            records = model.create([vals, ...])
    """
    warnings.warn(
        f"The model {method.__module__} is not overriding the create method in batch",
        DeprecationWarning
    )
    wrapper = _model_create_single(method) # pylint: disable=no-value-for-parameter
    wrapper._api = 'model_create'
    return wrapper

def model(method: T) -> T:
    """ Decorate a record-style method where ``self`` is a recordset, but its
        contents is not relevant, only the model is. Such a method::

            @api.model
            def method(self, args):
                ...

    """
    if method.__name__ == 'create':
        return model_create_single(method)
    method._api = 'model'
    return method