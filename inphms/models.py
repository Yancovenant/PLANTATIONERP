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
# from .api import NewId
# from .exceptions import AccessError, MissingError, ValidationError, UserError
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
    from inphms.api import IdType

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