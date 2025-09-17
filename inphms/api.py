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
# from .tools import , , , , Query, 
from .tools import frozendict, lazy_property, SQL, OrderedSet, clean_context
# from .tools.translate import get_translation, get_translated_module, LazyGettext
from inphms.tools.misc import StackMap

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


# sentinel value for optional parameters
NOTHING = object()
EMPTY_DICT = frozendict()


class Cache:
    """ Implementation of the cache of records.

    For most fields, the cache is simply a mapping from a record and a field to
    a value.  In the case of context-dependent fields, the mapping also depends
    on the environment of the given record.  For the sake of performance, the
    cache is first partitioned by field, then by record.  This makes some
    common ORM operations pretty fast, like determining which records have a
    value for a given field, or invalidating a given field on all possible
    records.

    The cache can also mark some entries as "dirty".  Dirty entries essentially
    marks values that are different from the database.  They represent database
    updates that haven't been done yet.  Note that dirty entries only make
    sense for stored fields.  Note also that if a field is dirty on a given
    record, and the field is context-dependent, then all the values of the
    record for that field are considered dirty.  For the sake of consistency,
    the values that should be in the database must be in a context where all
    the field's context keys are ``None``.
    """
    __slots__ = ('_data', '_dirty', '_patches')

    def __init__(self):
        # {field: {record_id: value}, field: {context_key: {record_id: value}}}
        self._data = defaultdict(dict)

        # {field: set[id]} stores the fields and ids that are changed in the
        # cache, but not yet written in the database; their changed values are
        # in `_data`
        self._dirty = defaultdict(OrderedSet)

        # {field: {record_id: ids}} record ids to be added to the values of
        # x2many fields if they are not in cache yet
        self._patches = defaultdict(lambda: defaultdict(list))


class Transaction:
    """ A object holding ORM data structures for a transaction. """
    __slots__ = ('_Transaction__file_open_tmp_paths', 'cache', 'envs', 'protected', 'registry', 'tocompute')

    def __init__(self, registry):
        self.registry = registry
        # weak set of environments
        self.envs = WeakSet()
        self.envs.data = OrderedSet()  # make the weakset OrderedWeakSet
        # cache for all records
        self.cache = Cache()
        # fields to protect {field: ids}
        self.protected = StackMap()
        # pending computations {field: ids}
        self.tocompute = defaultdict(OrderedSet)
        # temporary directories (managed in odoo.tools.file_open_temporary_directory)
        self.__file_open_tmp_paths = ()  # noqa: PLE0237


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
    cr: BaseCursor
    uid: int
    context: frozendict
    su: bool
    registry: Registry
    cache: Cache
    transaction: Transaction

    def reset(self):
        """ Reset the transaction, see :meth:`Transaction.reset`. """
        self.transaction.reset()
    
    def __new__(cls, cr, uid, context, su=False, uid_origin=None):
        assert isinstance(cr, BaseCursor)
        print("creating env", cr, uid, context, su, uid_origin)
        if uid == SUPERUSER_ID:
            su = True

        # isinstance(uid, int) is to handle `RequestUID`
        uid_origin = uid_origin or (uid if isinstance(uid, int) else None)
        if uid_origin == SUPERUSER_ID:
            uid_origin = None

        # determine transaction object
        transaction = cr.transaction
        if transaction is None:
            transaction = cr.transaction = Transaction(Registry(cr.dbname))

        # if env already exists, return it
        for env in transaction.envs:
            if (env.cr, env.uid, env.su, env.uid_origin, env.context) == (cr, uid, su, uid_origin, context):
                return env

        # otherwise create environment, and add it in the set
        self = object.__new__(cls)
        self.cr, self.uid, self.su, self.uid_origin = cr, uid, su, uid_origin
        self.context = frozendict(context)
        self.transaction = transaction
        self.registry = transaction.registry
        self.cache = transaction.cache

        self._cache_key = {}                    # memo {field: cache_key}
        self._protected = transaction.protected

        transaction.envs.add(self)
        return self
    
    #
    # Mapping methods
    #

    def __contains__(self, model_name):
        """ Test whether the given model exists. """
        return model_name in self.registry

    def __getitem__(self, model_name: str) -> BaseModel:
        """ Return an empty recordset from the given model. """
        return self.registry[model_name](self, (), ())

    def __iter__(self):
        """ Return an iterator on model names. """
        return iter(self.registry)

    def __len__(self):
        """ Return the size of the model registry. """
        return len(self.registry)

    def __eq__(self, other):
        return self is other

    def __ne__(self, other):
        return self is not other

    def __hash__(self):
        return object.__hash__(self)
    
    def __call__(self, cr=None, user=None, context=None, su=None):
        """ Return an environment based on ``self`` with modified parameters.

        :param cr: optional database cursor to change the current cursor
        :type cursor: :class:`~odoo.sql_db.Cursor`
        :param user: optional user/user id to change the current user
        :type user: int or :class:`res.users record<~odoo.addons.base.models.res_users.Users>`
        :param dict context: optional context dictionary to change the current context
        :param bool su: optional boolean to change the superuser mode
        :returns: environment with specified args (new or existing one)
        :rtype: :class:`Environment`
        """
        print("calling env", cr, user, context, su)
        cr = self.cr if cr is None else cr
        uid = self.uid if user is None else int(user)
        if context is None:
            context = clean_context(self.context) if su and not self.su else self.context
        su = (user is None and self.su) if su is None else su
        return Environment(cr, uid, context, su, self.uid_origin)
    
    def ref(self, xml_id, raise_if_not_found=True):
        """ Return the record corresponding to the given ``xml_id``.

        :param str xml_id: record xml_id, under the format ``<module.id>``
        :param bool raise_if_not_found: whether the method should raise if record is not found
        :returns: Found record or None
        :raise ValueError: if record wasn't found and ``raise_if_not_found`` is True
        """
        res_model, res_id = self['ir.model.data']._xmlid_to_res_model_res_id(
            xml_id, raise_if_not_found=raise_if_not_found
        )

        if res_model and res_id:
            record = self[res_model].browse(res_id)
            if record.exists():
                return record
            if raise_if_not_found:
                raise ValueError('No record found for unique ID %s. It may have been deleted.' % (xml_id))
        return None
    

def private(method): #ichecked
    """ Decorate a record-style method to indicate that the method cannot be
        called using RPC. Example::

            @api.private
            def method(self, args):
                ...

        If you have business methods that should not be called over RPC, you
        should prefix them with "_". This decorator may be used in case of
        existing public methods that become non-RPC callable or for ORM
        methods.
    """
    method._api_private = True
    return method

def propagate(method1, method2): #ichecked
    """ Propagate decorators from ``method1`` to ``method2``, and return the
        resulting method.
    """
    if method1:
        for attr in ('_returns',):
            if hasattr(method1, attr) and not hasattr(method2, attr):
                print('propagating', attr)
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


# The following attributes are used, and reflected on wrapping methods:
#  - method._constrains: set by @constrains, specifies constraint dependencies
#  - method._depends: set by @depends, specifies compute dependencies
#  - method._returns: set by @returns, specifies return model
#  - method._onchange: set by @onchange, specifies onchange fields
#  - method.clear_cache: set by @ormcache, used to clear the cache
#  - method._ondelete: set by @ondelete, used to raise errors for unlink operations
#
# On wrapping method only:
#  - method._api: decorator function, used for re-applying decorator
#

def attrsetter(attr, value):
    """ Return a function that sets ``attr`` on its argument and returns it. """
    return lambda method: setattr(method, attr, value) or method

def depends(*args: str) -> Callable[[T], T]:
    """ Return a decorator that specifies the field dependencies of a "compute"
        method (for new-style function fields). Each argument must be a string
        that consists in a dot-separated sequence of field names::

            pname = fields.Char(compute='_compute_pname')

            @api.depends('partner_id.name', 'partner_id.is_company')
            def _compute_pname(self):
                for record in self:
                    if record.partner_id.is_company:
                        record.pname = (record.partner_id.name or "").upper()
                    else:
                        record.pname = record.partner_id.name

        One may also pass a single function as argument. In that case, the
        dependencies are given by calling the function with the field's model.
    """
    if args and callable(args[0]):
        args = args[0]
    elif any('id' in arg.split('.') for arg in args):
        raise NotImplementedError("Compute method cannot depend on field 'id'.")
    return attrsetter('_depends', args)


_create_logger = logging.getLogger(__name__ + '.create')

@decorator
def _model_create_single(create, self, arg): #ichecked
    # 'create' expects a dict and returns a record
    if isinstance(arg, Mapping):
        return create(self, arg)
    if len(arg) > 1:
        _create_logger.debug("%s.create() called with %d dicts", self, len(arg))
    return self.browse().concat(*(create(self, vals) for vals in arg))

def model_create_single(method: T) -> T: #ichecked
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

def model(method: T) -> T: #ichecked
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


# keep those imports here in order to handle cyclic dependencies correctly
from inphms import SUPERUSER_ID
from inphms.modules.registry import Registry
from .sql_db import BaseCursor