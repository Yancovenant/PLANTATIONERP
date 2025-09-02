# Part of Inphms, see License file for full copyright and licensing details.

"""
    Object Relational Mapping module:
     * Hierarchical structure
     * Constraints consistency and validation
     * Object metadata depends on its status
     * Optimised processing by complex query (multiple actions at once)
     * Default field values
     * Permissions optimisation
     * Persistent object: DB postgresql
     * Data conversion
     * Multi-level caching system
     * Two different inheritance mechanisms
     * Rich set of field types:
          - classical (varchar, integer, boolean, ...)
          - relational (one2many, many2one, many2many)
          - functional

"""
from __future__ import annotations

import collections
import contextlib
import datetime
import functools
import inspect
import itertools
import io
import json
import logging
import operator
import pytz
import re
import uuid
import warnings
from collections import defaultdict, deque
from collections.abc import MutableMapping, Callable
from contextlib import closing
from inspect import getmembers
from operator import attrgetter, itemgetter

import babel
import babel.dates
import dateutil.relativedelta
import psycopg2
import psycopg2.extensions
from psycopg2.extras import Json

import inphms
from . import SUPERUSER_ID
from . import api
from . import tools
from .api import NewId
from .exceptions import AccessError, MissingError, ValidationError, UserError
# from .tools import (
#     clean_context, config, date_utils, discardattr,
#     DEFAULT_SERVER_DATE_FORMAT, DEFAULT_SERVER_DATETIME_FORMAT, format_list,
#     frozendict, get_lang, lazy_classproperty, OrderedSet,
#     ormcache, partition, Query, split_every, unique,
#     SQL, sql, groupby,
# )
from .tools import (
    frozendict, lazy_classproperty, config, SQL
)
from .tools.lru import LRU
# from .tools.misc import LastOrderedSet, ReversedIterable, unquote
# from .tools.translate import _, LazyTranslate

import typing
if typing.TYPE_CHECKING:
    from collections.abc import Reversible
    from .modules.registry import Registry
    # from inphms.api import Self, ValuesType, IdType
    from inphms.api import IdType, Self

# _lt = LazyTranslate('base')
_logger = logging.getLogger(__name__)
_unlink = logging.getLogger(__name__ + '.unlink')

regex_alphanumeric = re.compile(r'^[a-z0-9_]+$')
regex_order = re.compile(r'''
    ^
    (\s*
        (?P<term>((?P<field>[a-z0-9_]+|"[a-z0-9_]+")(\.(?P<property>[a-z0-9_]+))?(:(?P<func>[a-z_]+))?))
        (\s+(?P<direction>desc|asc))?
        (\s+(?P<nulls>nulls\ first|nulls\ last))?
        \s*
        (,|$)
    )+
    (?<!,)
    $
''', re.IGNORECASE | re.VERBOSE)
regex_object_name = re.compile(r'^[a-z0-9_.]+$')
regex_pg_name = re.compile(r'^[a-z_][a-z0-9_$]*$', re.I)
regex_field_agg = re.compile(r'(\w+)(?::(\w+)(?:\((\w+)\))?)?')  # For read_group
regex_read_group_spec = re.compile(r'(\w+)(\.(\w+))?(?::(\w+))?$')  # For _read_group

AUTOINIT_RECALCULATE_STORED_FIELDS = 1000
GC_UNLINK_LIMIT = 100_000

INSERT_BATCH_SIZE = 100
UPDATE_BATCH_SIZE = 100
SQL_DEFAULT = psycopg2.extensions.AsIs("DEFAULT")


class MetaModel(api.Meta):
    """ The metaclass of all model classes.
        Its main purpose is to register the models per module.
    """
    module_to_models = defaultdict(list)

    def __new__(meta, name, bases, attrs):
        # this prevents assignment of non-fields on recordsets
        attrs.setdefault('__slots__', ())
        # this collects the fields defined on the class (via Field.__set_name__())
        attrs.setdefault('_field_definitions', [])

        if attrs.get('_register', True):
            # determine '_module'
            if '_module' not in attrs:
                module = attrs['__module__']
                assert module.startswith('inphms.addons.'), \
                    f"Invalid import of {module}.{name}, it should start with 'inphms.addons'."
                attrs['_module'] = module.split('.')[2]

            # determine model '_name' and normalize '_inherits'
            inherit = attrs.get('_inherit', ())
            if isinstance(inherit, str):
                inherit = attrs['_inherit'] = [inherit]
            if '_name' not in attrs:
                attrs['_name'] = inherit[0] if len(inherit) == 1 else name

        return super().__new__(meta, name, bases, attrs)

    def __init__(self, name, bases, attrs):
        super().__init__(name, bases, attrs)

        if '__init__' in attrs and len(inspect.signature(attrs['__init__']).parameters) != 4:
            _logger.warning("The method %s.__init__ doesn't match the new signature in module %s", name, attrs.get('__module__'))

        if not attrs.get('_register', True):
            return

        # Remember which models to instantiate for this module.
        if self._module:
            self.module_to_models[self._module].append(self)

        if not self._abstract and self._name not in self._inherit:
            # this class defines a model: add magic fields
            def add(name, field):
                setattr(self, name, field)
                field.__set_name__(self, name)

            def add_default(name, field):
                if name not in attrs:
                    setattr(self, name, field)
                    field.__set_name__(self, name)
            
            add('id', fields.Id(automatic=True))
            add_default('display_name', fields.Char(
                string='Display Name', automatic=True,
                compute='_compute_display_name',
                search='_search_display_name',
            ))

            if attrs.get('_log_access', self._auto):
                add_default('create_uid', fields.Many2one(
                    'res.users', string='Created by', automatic=True, readonly=True))
                add_default('create_date', fields.Datetime(
                    string='Created on', automatic=True, readonly=True))
                add_default('write_uid', fields.Many2one(
                    'res.users', string='Last Updated by', automatic=True, readonly=True))
                add_default('write_date', fields.Datetime(
                    string='Last Updated on', automatic=True, readonly=True))


# maximum number of prefetched records
PREFETCH_MAX = 1000

# special columns automatically created by the ORM
LOG_ACCESS_COLUMNS = ['create_uid', 'create_date', 'write_uid', 'write_date']
MAGIC_COLUMNS = ['id'] + LOG_ACCESS_COLUMNS

# read_group stuff
READ_GROUP_TIME_GRANULARITY = {
    'hour': dateutil.relativedelta.relativedelta(hours=1),
    'day': dateutil.relativedelta.relativedelta(days=1),
    'week': datetime.timedelta(days=7),
    'month': dateutil.relativedelta.relativedelta(months=1),
    'quarter': dateutil.relativedelta.relativedelta(months=3),
    'year': dateutil.relativedelta.relativedelta(years=1)
}

READ_GROUP_NUMBER_GRANULARITY = {
    'year_number': 'year',
    'quarter_number': 'quarter',
    'month_number': 'month',
    'iso_week_number': 'week',  # ISO week number because anything else than ISO is nonsense
    'day_of_year': 'doy',
    'day_of_month': 'day',
    'day_of_week': 'dow',
    'hour_number': 'hour',
    'minute_number': 'minute',
    'second_number': 'second',
}

READ_GROUP_ALL_TIME_GRANULARITY = READ_GROUP_TIME_GRANULARITY | READ_GROUP_NUMBER_GRANULARITY



# valid SQL aggregation functions
READ_GROUP_AGGREGATE = {
    'sum': lambda table, expr: SQL('SUM(%s)', expr),
    'avg': lambda table, expr: SQL('AVG(%s)', expr),
    'max': lambda table, expr: SQL('MAX(%s)', expr),
    'min': lambda table, expr: SQL('MIN(%s)', expr),
    'bool_and': lambda table, expr: SQL('BOOL_AND(%s)', expr),
    'bool_or': lambda table, expr: SQL('BOOL_OR(%s)', expr),
    'array_agg': lambda table, expr: SQL('ARRAY_AGG(%s ORDER BY %s)', expr, SQL.identifier(table, 'id')),
    # 'recordset' aggregates will be post-processed to become recordsets
    'recordset': lambda table, expr: SQL('ARRAY_AGG(%s ORDER BY %s)', expr, SQL.identifier(table, 'id')),
    'count': lambda table, expr: SQL('COUNT(%s)', expr),
    'count_distinct': lambda table, expr: SQL('COUNT(DISTINCT %s)', expr),
}

READ_GROUP_DISPLAY_FORMAT = {
    # Careful with week/year formats:
    #  - yyyy (lower) must always be used, *except* for week+year formats
    #  - YYYY (upper) must always be used for week+year format
    #         e.g. 2006-01-01 is W52 2005 in some locales (de_DE),
    #                         and W1 2006 for others
    #
    # Mixing both formats, e.g. 'MMM YYYY' would yield wrong results,
    # such as 2006-01-01 being formatted as "January 2005" in some locales.
    # Cfr: http://babel.pocoo.org/en/latest/dates.html#date-fields
    'hour': 'hh:00 dd MMM',
    'day': 'dd MMM yyyy', # yyyy = normal year
    'week': "'W'w YYYY",  # w YYYY = ISO week-year
    'month': 'MMMM yyyy',
    'quarter': 'QQQ yyyy',
    'year': 'yyyy',
}

# THE DEFINITION AND REGISTRY CLASSES
#
# The framework deals with two kinds of classes for models: the "definition"
# classes and the "registry" classes.
#
# The "definition" classes are the ones defined in modules source code: they
# define models and extend them.  Those classes are essentially "static", for
# whatever that means in Python.  The only exception is custom models: their
# definition class is created dynamically.
#
# The "registry" classes are the ones you find in the registry.  They are the
# actual classes of the recordsets of their model.  The "registry" class of a
# model is created dynamically when the registry is built.  It inherits (in the
# Python sense) from all the definition classes of the model, and possibly other
# registry classes (when the model inherits from another model).  It also
# carries model metadata inferred from its parent classes.
#
#
# THE REGISTRY CLASS OF A MODEL
#
# In the simplest case, a model's registry class inherits from all the classes
# that define the model in a flat hierarchy.  Consider the model definition
# below.  The registry class of model 'a' inherits from the definition classes
# A1, A2, A3, in reverse order, to match the expected overriding order.  The
# registry class carries inferred metadata that is shared between all the
# model's instances for a given registry.
#
#       class A1(Model):                      Model
#           _name = 'a'                       / | \
#                                            A3 A2 A1   <- definition classes
#       class A2(Model):                      \ | /
#           _inherit = 'a'                      a       <- registry class: registry['a']
#                                               |
#       class A3(Model):                     records    <- model instances, like env['a']
#           _inherit = 'a'
#
# Note that when the model inherits from another model, we actually make the
# registry classes inherit from each other, so that extensions to an inherited
# model are visible in the registry class of the child model, like in the
# following example.
#
#       class A1(Model):
#           _name = 'a'                       Model
#                                            / / \ \
#       class B1(Model):                    / /   \ \
#           _name = 'b'                    / A2   A1 \
#                                         B2  \   /  B1
#       class B2(Model):                   \   \ /   /
#           _name = 'b'                     \   a   /
#           _inherit = ['a', 'b']            \  |  /
#                                             \ | /
#       class A2(Model):                        b
#           _inherit = 'a'
#
#
# THE FIELDS OF A MODEL
#
# The fields of a model are given by the model's definition classes, inherited
# models ('_inherit' and '_inherits') and other parties, like custom fields.
# Note that a field can be partially overridden when it appears on several
# definition classes of its model.  In that case, the field's final definition
# depends on the presence or absence of each definition class, which itself
# depends on the modules loaded in the registry.
#
# By design, the registry class has access to all the fields on the model's
# definition classes.  When possible, the field is used directly from the
# model's registry class.  There are a number of cases where the field cannot be
# used directly:
#  - the field is related (and bits may not be shared);
#  - the field is overridden on definition classes;
#  - the field is defined for another model (and accessible by mixin).
#
# The last case prevents sharing the field, because the field object is specific
# to a model, and is used as a key in several key dictionaries, like the record
# cache and pending computations.
#
# Setting up a field on its definition class helps saving memory and time.
# Indeed, when sharing is possible, the field's setup is almost entirely done
# where the field was defined.  It is thus done when the definition class was
# created, and it may be reused across registries.
#
# In the example below, the field 'foo' appears once on its model's definition
# classes.  Assuming that it is not related, that field can be set up directly
# on its definition class.  If the model appears in several registries, the
# field 'foo' is effectively shared across registries.
#
#       class A1(Model):                      Model
#           _name = 'a'                        / \
#           foo = ...                         /   \
#           bar = ...                       A2     A1
#                                            bar    foo, bar
#       class A2(Model):                      \   /
#           _inherit = 'a'                     \ /
#           bar = ...                           a
#                                                bar
#
# On the other hand, the field 'bar' is overridden in its model's definition
# classes.  In that case, the framework recreates the field on the model's
# registry class.  The field's setup will be based on its definitions, and will
# not be shared across registries.
#
# The so-called magic fields ('id', 'display_name', ...) used to be added on
# registry classes.  But doing so prevents them from being shared.  So instead,
# we add them on definition classes that define a model without extending it.
# This increases the number of fields that are shared across registries.

def is_definition_class(cls):
    """ Return whether ``cls`` is a model definition class. """
    return isinstance(cls, MetaModel) and getattr(cls, 'pool', None) is None

class BaseModel(metaclass=MetaModel):
    """Base class for Inphms models.

        Inphms models are created by inheriting one of the following:

        *   :class:`Model` for regular database-persisted models

        *   :class:`TransientModel` for temporary data, stored in the database but
            automatically vacuumed every so often

        *   :class:`AbstractModel` for abstract super classes meant to be shared by
            multiple inheriting models

        The system automatically instantiates every model once per database. Those
        instances represent the available models on each database, and depend on
        which modules are installed on that database. The actual class of each
        instance is built from the Python classes that create and inherit from the
        corresponding model.

        Every model instance is a "recordset", i.e., an ordered collection of
        records of the model. Recordsets are returned by methods like
        :meth:`~.browse`, :meth:`~.search`, or field accesses. Records have no
        explicit representation: a record is represented as a recordset of one
        record.

        To create a class that should not be instantiated,
        the :attr:`~inphms.models.BaseModel._register` attribute may be set to False.
    """
    __slots__ = ['env', '_ids', '_prefetch_ids']

    env: api.Environment
    id: IdType | typing.Literal[False]
    display_name: str | typing.Literal[False]
    pool: Registry

    _fields: dict[str, Field]
    _auto = False
    """Whether a database table should be created.
    If set to ``False``, override :meth:`~inphms.models.BaseModel.init`
    to create the database table.

    Automatically defaults to `True` for :class:`Model` and
    :class:`TransientModel`, `False` for :class:`AbstractModel`.

    .. tip:: To create a model without any table, inherit
            from :class:`~inphms.models.AbstractModel`.
    """
    _register = False           #: registry visibility
    _abstract = True
    """ Whether the model is *abstract*.

    .. seealso:: :class:`AbstractModel`
    """
    _transient = False
    """ Whether the model is *transient*.

    .. seealso:: :class:`TransientModel`
    """

    _name: str | None = None            #: the model name (in dot-notation, module namespace)
    _description: str | None = None     #: the model's informal name
    _module = None                      #: the model's module (in the Odoo sense)
    _custom = False                     #: should be True for custom models only

    _inherit: str | list[str] | tuple[str, ...] = ()
    """Python-inherited models:

    :type: str or list(str) or tuple(str)

    .. note::

        * If :attr:`._name` is set, name(s) of parent models to inherit from
        * If :attr:`._name` is unset, name of a single model to extend in-place
    """
    _inherits = frozendict()
    """dictionary {'parent_model': 'm2o_field'} mapping the _name of the parent business
    objects to the names of the corresponding foreign key fields to use::

      _inherits = {
          'a.model': 'a_field_id',
          'b.model': 'b_field_id'
      }

    implements composition-based inheritance: the new model exposes all
    the fields of the inherited models but stores none of them:
    the values themselves remain stored on the linked record.

    .. warning::

      if multiple fields with the same name are defined in the
      :attr:`~inphms.models.Model._inherits`-ed models, the inherited field will
      correspond to the last one (in the inherits list order).
    """
    _table = None                   #: SQL table name used by model if :attr:`_auto`
    _table_query = None             #: SQL expression of the table's content (optional)
    _sql_constraints: list[tuple[str, str, str]] = []   #: SQL constraints [(name, sql_def, message)]

    _rec_name = None                #: field to use for labeling records, default: ``name``
    _rec_names_search: list[str] | None = None    #: fields to consider in ``name_search``
    _order = 'id'                   #: default order field for searching results
    _parent_name = 'parent_id'      #: the many2one field used as parent field
    _parent_store = False
    """set to True to compute parent_path field.

    Alongside a :attr:`~.parent_path` field, sets up an indexed storage
    of the tree structure of records, to enable faster hierarchical queries
    on the records of the current model using the ``child_of`` and
    ``parent_of`` domain operators.
    """
    _active_name = None
    """field to use for active records, automatically set to either ``"active"``
    or ``"x_active"``.
    """
    _fold_name = 'fold'         #: field to determine folded groups in kanban views

    _translate = True           # False disables translations export for this model (Old API)
    _check_company_auto = False
    """On write and create, call ``_check_company`` to ensure companies
    consistency on the relational fields having ``check_company=True``
    as attribute.
    """

    _allow_sudo_commands = True
    """Allow One2many and Many2many Commands targeting this model in an environment using `sudo()` or `with_user()`.
    By disabling this flag, security-sensitive models protect themselves
    against malicious manipulation of One2many or Many2many fields
    through an environment using `sudo` or a more privileged user.
    """

    _depends = frozendict()
    """dependencies of models backed up by SQL views
    ``{model_name: field_names}``, where ``field_names`` is an iterable.
    This is only used to determine the changes to flush to database before
    executing ``search()`` or ``read_group()``. It won't be used for cache
    invalidation or recomputing fields.
    """

    # default values for _transient_vacuum()
    _transient_max_count = lazy_classproperty(lambda _: config.get('osv_memory_count_limit'))
    "maximum number of transient records, unlimited if ``0``"
    _transient_max_hours = lazy_classproperty(lambda _: config.get('transient_age_limit'))
    "maximum idle lifetime (in hours), unlimited if ``0``"

    #
    # Instance creation
    #
    # An instance represents an ordered collection of records in a given
    # execution environment. The instance object refers to the environment, and
    # the records themselves are represented by their cache dictionary. The 'id'
    # of each record is found in its corresponding cache dictionary.
    #
    # This design has the following advantages:
    #  - cache access is direct and thus fast;
    #  - one can consider records without an 'id' (see new records);
    #  - the global cache is only an index to "resolve" a record 'id'.
    #
    def __init__(self, env: api.Environment, ids: tuple[IdType, ...], prefetch_ids: Reversible[IdType]):
        """ Create a recordset instance.

        :param env: an environment
        :param ids: a tuple of record ids
        :param prefetch_ids: a reversible iterable of record ids (for prefetching)
        """
        self.env = env
        self._ids = ids
        self._prefetch_ids = prefetch_ids
    
    #
    # Internal properties, for manipulating the instance's implementation
    #

    # @property
    # def ids(self) -> list[int]:
    #     """ Return the list of actual record ids corresponding to ``self``. """
    #     return list(origin_ids(self._ids))

    # backward-compatibility with former browse records
    _cr = property(lambda self: self.env.cr)
    _uid = property(lambda self: self.env.uid)
    _context = property(lambda self: self.env.context)
    
    #
    # Conversion methods
    #

    @api.private
    def with_env(self, env: api.Environment) -> Self: #ichecked
        """Return a new version of this recordset attached to the provided environment.

        :param env:
        :type env: :class:`~inphms.api.Environment`

        .. note::
            The returned recordset has the same prefetch object as ``self``.
        """
        return self.__class__(env, self._ids, self._prefetch_ids)

    @api.private
    def with_context(self, *args, **kwargs) -> Self: #ichecked
        """ with_context([context][, **overrides]) -> Model

        Returns a new version of this recordset attached to an extended
        context.

        The extended context is either the provided ``context`` in which
        ``overrides`` are merged or the *current* context in which
        ``overrides`` are merged e.g.::

            # current context is {'key1': True}
            r2 = records.with_context({}, key2=True)
            # -> r2._context is {'key2': True}
            r2 = records.with_context(key2=True)
            # -> r2._context is {'key1': True, 'key2': True}

        .. note:

            The returned recordset has the same prefetch object as ``self``.
        """  # noqa: RST210
        if (args and 'force_company' in args[0]) or 'force_company' in kwargs:
            _logger.warning(
                "Context key 'force_company' is no longer supported. "
                "Use with_company(company) instead.",
                stack_info=True,
            )
        if (args and 'company' in args[0]) or 'company' in kwargs:
            _logger.warning(
                "Context key 'company' is not recommended, because "
                "of its special meaning in @depends_context.",
                stack_info=True,
            )
        context = dict(args[0] if args else self._context, **kwargs)
        if 'allowed_company_ids' not in context and 'allowed_company_ids' in self._context:
            # Force 'allowed_company_ids' to be kept when context is overridden
            # without 'allowed_company_ids'
            context['allowed_company_ids'] = self._context['allowed_company_ids']
        return self.with_env(self.env(context=context))

    #
    # "Dunder" methods
    #

    def __bool__(self):
        """ Test whether ``self`` is nonempty. """
        return True if self._ids else False  # fast version of bool(self._ids)

    def __len__(self):
        """ Return the size of ``self``. """
        return len(self._ids)
    
    def __iter__(self) -> typing.Iterator[Self]:
        """ Return an iterator over ``self``. """
        if len(self._ids) > PREFETCH_MAX and self._prefetch_ids is self._ids:
            for ids in self.env.cr.split_for_in_conditions(self._ids):
                for id_ in ids:
                    yield self.__class__(self.env, (id_,), ids)
        else:
            for id_ in self._ids:
                yield self.__class__(self.env, (id_,), self._prefetch_ids)
    
    def __reversed__(self) -> typing.Iterator[Self]:
        """ Return an reversed iterator over ``self``. """
        if len(self._ids) > PREFETCH_MAX and self._prefetch_ids is self._ids:
            for ids in self.env.cr.split_for_in_conditions(reversed(self._ids)):
                for id_ in ids:
                    yield self.__class__(self.env, (id_,), ids)
        elif self._ids:
            prefetch_ids = ReversedIterable(self._prefetch_ids)
            for id_ in reversed(self._ids):
                yield self.__class__(self.env, (id_,), prefetch_ids)
    
    def __contains__(self, item):
        """ Test whether ``item`` (record or field name) is an element of ``self``.
            In the first case, the test is fully equivalent to::

                any(item == record for record in self)
        """
        try:
            if self._name == item._name:
                return len(item) == 1 and item.id in self._ids
            raise TypeError(f"inconsistent models in: {item} in {self}")
        except AttributeError:
            if isinstance(item, str):
                return item in self._fields
            raise TypeError(f"unsupported operand types in: {item!r} in {self}")

    def __add__(self, other) -> Self:
        """ Return the concatenation of two recordsets. """
        return self.concat(other)
    
    def __sub__(self, other) -> Self:
        """ Return the recordset of all the records in ``self`` that are not in
            ``other``. Note that recordset order is preserved.
        """
        try:
            if self._name != other._name:
                raise TypeError(f"inconsistent models in: {self} - {other}")
            other_ids = set(other._ids)
            return self.browse([id for id in self._ids if id not in other_ids])
        except AttributeError:
            raise TypeError(f"unsupported operand types in: {self} - {other!r}")
    
    def __and__(self, other) -> Self:
        """ Return the intersection of two recordsets.
            Note that first occurrence order is preserved.
        """
        try:
            if self._name != other._name:
                raise TypeError(f"inconsistent models in: {self} & {other}")
            other_ids = set(other._ids)
            return self.browse(OrderedSet(id for id in self._ids if id in other_ids))
        except AttributeError:
            raise TypeError(f"unsupported operand types in: {self} & {other!r}")

    @api.private
    def union(self, *args) -> Self:
        return self.browse(OrderedSet(ids))

    def __or__(self, other) -> Self:
        """ Return the union of two recordsets.
            Note that first occurrence order is preserved.
        """
        return self.union(other)

    def __eq__(self, other):
        """ Test whether two recordsets are equivalent (up to reordering). """
        try:
            return self._name == other._name and set(self._ids) == set(other._ids)
        except AttributeError:
            if other:
                warnings.warn(f"unsupported operand type(s) for \"==\": '{self._name}()' == '{other!r}'", stacklevel=2)
        return NotImplemented

    def __lt__(self, other):
        try:
            if self._name == other._name:
                return set(self._ids) < set(other._ids)
        except AttributeError:
            pass
        return NotImplemented

    def __le__(self, other):
        try:
            if self._name == other._name:
                # these are much cheaper checks than a proper subset check, so
                # optimise for checking if a null or singleton are subsets of a
                # recordset
                if not self or self in other:
                    return True
                return set(self._ids) <= set(other._ids)
        except AttributeError:
            pass
        return NotImplemented

    def __gt__(self, other):
        try:
            if self._name == other._name:
                return set(self._ids) > set(other._ids)
        except AttributeError:
            pass
        return NotImplemented

    def __ge__(self, other):
        try:
            if self._name == other._name:
                if not other or other in self:
                    return True
                return set(self._ids) >= set(other._ids)
        except AttributeError:
            pass
        return NotImplemented

    def __int__(self):
        return self.id or 0

    def __repr__(self):
        return f"{self._name}{self._ids!r}"

    def __hash__(self):
        return hash((self._name, frozenset(self._ids)))

    def __deepcopy__(self, memo):
        return self
    
    @typing.overload
    def __getitem__(self, key: int | slice) -> Self: ...

    @typing.overload
    def __getitem__(self, key: str) -> typing.Any: ...

    def __getitem__(self, key):
        """ If ``key`` is an integer or a slice, return the corresponding record
            selection as an instance (attached to ``self.env``).
            Otherwise read the field ``key`` of the first record in ``self``.

            Examples::

                inst = model.search(dom)    # inst is a recordset
                r4 = inst[3]                # fourth record in inst
                rs = inst[10:20]            # subset of inst
                nm = rs['name']             # name of first record in inst
        """
        if isinstance(key, str):
            # important: one must call the field's getter
            return self._fields[key].__get__(self)
        elif isinstance(key, slice):
            return self.browse(self._ids[key])
        else:
            return self.browse((self._ids[key],))
    
    def __setitem__(self, key, value):
        """ Assign the field ``key`` to ``value`` in record ``self``. """
        # important: one must call the field's setter
        return self._fields[key].__set__(self, value)
    
    #
    # Cache and recomputation management
    #


collections.abc.Set.register(BaseModel)
# not exactly true as BaseModel doesn't have index or count
collections.abc.Sequence.register(BaseModel)


AbstractModel = BaseModel

# DONE
class Model(AbstractModel):
    """ Main super-class for regular database-persisted Inphms models.

    Inphms models are created by inheriting from this class::

        class user(Model):
            ...

    The system will later instantiate the class once per database (on
    which the class' module is installed).
    """
    _auto = True                # automatically create database backend
    _register = False           # not visible in ORM registry, meant to be python-inherited only
    _abstract = False           # not abstract
    _transient = False          # not transient



# keep those imports here to avoid dependency cycle errors
# pylint: disable=wrong-import-position
from . import fields
# from .osv import expression
# from .fields import Field, Datetime, Command
from .fields import Field, Datetime