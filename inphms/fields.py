# -*- coding: utf-8 -*-
# Part of Inphms, see License file for full copyright and licensing details.

""" High-level objects for fields. """
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time
from operator import attrgetter
from xmlrpc.client import MAXINT
import ast
import base64
import copy
import contextlib
import binascii
import enum
import itertools
import json
import logging
import uuid
import warnings

import psycopg2
import pytz
from markupsafe import Markup, escape as markup_escape
from psycopg2.extras import Json as PsycopgJson
from difflib import get_close_matches, unified_diff
from hashlib import sha256

# from .models import check_property_field_value_name
from .netsvc import ColoredFormatter, GREEN, RED, DEFAULT, COLOR_PATTERN
# from .tools import (
#     float_repr, float_round, float_compare, float_is_zero, human_size,
#     OrderedSet, sql, , , , ,
#     image_process, merge_sequences, is_list_of,
#     html_normalize, html_sanitize,
#     ,
#     ,
# )
from .tools import (
    SQL, lazy_property, unique, 
    DEFAULT_SERVER_DATE_FORMAT as DATE_FORMAT,
    DEFAULT_SERVER_DATETIME_FORMAT as DATETIME_FORMAT,
    date_utils,
)
from .tools.sql import pg_varchar
# from .tools.mimetypes import guess_mimetype
# from .tools.misc import unquote, has_list_types, Sentinel, SENTINEL
from .tools.misc import Sentinel, SENTINEL
# from .tools.translate import html_translate

from inphms import SUPERUSER_ID
from inphms.exceptions import CacheMiss
# from inphms.osv import expression

import typing
from inphms.api import ContextType, DomainType, IdType, NewId, M, T


DATE_LENGTH = len(date.today().strftime(DATE_FORMAT))
DATETIME_LENGTH = len(datetime.now().strftime(DATETIME_FORMAT))

# hacky-ish way to prevent access to a field through the ORM (except for sudo mode)
NO_ACCESS='.'

IR_MODELS = (
    'ir.model', 'ir.model.data', 'ir.model.fields', 'ir.model.fields.selection',
    'ir.model.relation', 'ir.model.constraint', 'ir.module.module',
)

COMPANY_DEPENDENT_FIELDS = (
    'char', 'float', 'boolean', 'integer', 'text', 'many2one', 'date', 'datetime', 'selection', 'html'
)

_logger = logging.getLogger(__name__)
_schema = logging.getLogger(__name__[:-7] + '.schema')

NoneType = type(None)


class MetaField(type):
    """ Metaclass for field classes. """
    by_type = {}

    def __init__(cls, name, bases, attrs):
        super().__init__(name, bases, attrs)
        if not hasattr(cls, 'type'):
            return

        if cls.type and cls.type not in MetaField.by_type:
            MetaField.by_type[cls.type] = cls

        # compute class attributes to avoid calling dir() on fields
        cls.related_attrs = []
        cls.description_attrs = []
        for attr in dir(cls):
            if attr.startswith('_related_'):
                cls.related_attrs.append((attr[9:], attr))
            elif attr.startswith('_description_'):
                cls.description_attrs.append((attr[13:], attr))

_global_seq = iter(itertools.count())

class Field(MetaField('DummyField', (object,), {}), typing.Generic[T]):
    """The field descriptor contains the field definition, and manages accesses
        and assignments of the corresponding field on records. The following
        attributes may be provided when instantiating a field:

        :param str string: the label of the field seen by users; if not
            set, the ORM takes the field name in the class (capitalized).

        :param str help: the tooltip of the field seen by users

        :param bool readonly: whether the field is readonly (default: ``False``)

            This only has an impact on the UI. Any field assignation in code will work
            (if the field is a stored field or an inversable one).

        :param bool required: whether the value of the field is required (default: ``False``)

        :param str index: whether the field is indexed in database, and the kind of index.
            Note: this has no effect on non-stored and virtual fields.
            The possible values are:

            * ``"btree"`` or ``True``: standard index, good for many2one
            * ``"btree_not_null"``: BTREE index without NULL values (useful when most
                                    values are NULL, or when NULL is never searched for)
            * ``"trigram"``: Generalized Inverted Index (GIN) with trigrams (good for full-text search)
            * ``None`` or ``False``: no index (default)

        :param default: the default value for the field; this is either a static
            value, or a function taking a recordset and returning a value; use
            ``default=None`` to discard default values for the field
        :type default: value or callable

        :param str groups: comma-separated list of group xml ids (string); this
            restricts the field access to the users of the given groups only

        :param bool company_dependent: whether the field value is dependent of the current company;

            The value is stored on the model table as jsonb dict with the company id as the key.

            The field's default values stored in model ir.default are used as fallbacks for
            unspecified values in the jsonb dict.

        :param bool copy: whether the field value should be copied when the record
            is duplicated (default: ``True`` for normal fields, ``False`` for
            ``one2many`` and computed fields, including property fields and
            related fields)

        :param bool store: whether the field is stored in database
            (default:``True``, ``False`` for computed fields)

        :param str aggregator: aggregate function used by :meth:`~odoo.models.Model.read_group`
            when grouping on this field.

            Supported aggregate functions are:

            * ``array_agg`` : values, including nulls, concatenated into an array
            * ``count`` : number of rows
            * ``count_distinct`` : number of distinct rows
            * ``bool_and`` : true if all values are true, otherwise false
            * ``bool_or`` : true if at least one value is true, otherwise false
            * ``max`` : maximum value of all values
            * ``min`` : minimum value of all values
            * ``avg`` : the average (arithmetic mean) of all values
            * ``sum`` : sum of all values

        :param str group_expand: function used to expand read_group results when grouping on
            the current field. For selection fields, ``group_expand=True`` automatically
            expands groups for all selection keys.

            .. code-block:: python

                @api.model
                def _read_group_selection_field(self, values, domain):
                    return ['choice1', 'choice2', ...] # available selection choices.

                @api.model
                def _read_group_many2one_field(self, records, domain):
                    return records + self.search([custom_domain])

        .. rubric:: Computed Fields

        :param str compute: name of a method that computes the field

            .. seealso:: :ref:`Advanced Fields/Compute fields <reference/fields/compute>`

        :param bool precompute: whether the field should be computed before record insertion
            in database.  Should be used to specify manually some fields as precompute=True
            when the field can be computed before record insertion.
            (e.g. avoid statistics fields based on search/read_group), many2one
            linking to the previous record, ... (default: `False`)

            .. warning::

                Precomputation only happens when no explicit value and no default
                value is provided to create().  This means that a default value
                disables the precomputation, even if the field is specified as
                precompute=True.

                Precomputing a field can be counterproductive if the records of the
                given model are not created in batch.  Consider the situation were
                many records are created one by one.  If the field is not
                precomputed, it will normally be computed in batch at the flush(),
                and the prefetching mechanism will help making the computation
                efficient.  On the other hand, if the field is precomputed, the
                computation will be made one by one, and will therefore not be able
                to take advantage of the prefetching mechanism.

                Following the remark above, precomputed fields can be interesting on
                the lines of a one2many, which are usually created in batch by the
                ORM itself, provided that they are created by writing on the record
                that contains them.

        :param bool compute_sudo: whether the field should be recomputed as superuser
            to bypass access rights (by default ``True`` for stored fields, ``False``
            for non stored fields)

        :param bool recursive: whether the field has recursive dependencies (the field
            ``X`` has a dependency like ``parent_id.X``); declaring a field recursive
            must be explicit to guarantee that recomputation is correct

        :param str inverse: name of a method that inverses the field (optional)

        :param str search: name of a method that implement search on the field (optional)

        :param str related: sequence of field names

        :param bool default_export_compatible: whether the field must be exported by default in an import-compatible export

            .. seealso:: :ref:`Advanced fields/Related fields <reference/fields/related>`
    """

    type: str                           # type of the field (string)
    relational = False                  # whether the field is a relational one
    translate = False                   # whether the field is translated

    write_sequence = 0  # field ordering for write()
    # Database column type (ident, spec) for non-company-dependent fields.
    # Company-dependent fields are stored as jsonb (see column_type).
    _column_type: typing.Tuple[str, str] | None = None

    _args__ = None                      # the parameters given to __init__()
    _module = None                      # the field's module name
    _modules = None                     # modules that define this field
    _setup_done = True                  # whether the field is completely set up
    _sequence = None                    # absolute ordering of the field
    _base_fields = ()                   # the fields defining self, in override order
    _extra_keys = ()                    # unknown attributes set on the field
    _direct = False                     # whether self may be used directly (shared)
    _toplevel = False                   # whether self is on the model's registry class

    automatic = False                   # whether the field is automatically created ("magic" field)
    inherited = False                   # whether the field is inherited (_inherits)
    inherited_field = None              # the corresponding inherited field

    name: str                           # name of the field
    model_name: str | None = None       # name of the model of this field
    comodel_name: str | None = None     # name of the model of values (if relational)

    store = True                        # whether the field is stored in database
    index = None                        # how the field is indexed in database
    manual = False                      # whether the field is a custom field
    copy = True                         # whether the field is copied over by BaseModel.copy()
    _depends = None                     # collection of field dependencies
    _depends_context = None             # collection of context key dependencies
    recursive = False                   # whether self depends on itself
    compute = None                      # compute(recs) computes field on recs
    compute_sudo = False                # whether field should be recomputed as superuser
    precompute = False                  # whether field has to be computed before creation
    inverse = None                      # inverse(recs) inverses field on recs
    search = None                       # search(recs, operator, value) searches on self
    related = None                      # sequence of field names, for related fields
    company_dependent = False           # whether ``self`` is company-dependent (property field)
    default = None                      # default(recs) returns the default value

    string: str | None = None           # field label
    export_string_translation = True    # whether the field label translations are exported
    help: str | None = None             # field tooltip
    readonly = False                    # whether the field is readonly
    required = False                    # whether the field is required
    groups: str | None = None           # csv list of group xml ids
    change_default = False              # whether the field may trigger a "user-onchange"

    related_field = None                # corresponding related field
    aggregator = None                   # operator for aggregating values
    group_expand = None                 # name of method to expand groups in read_group()
    prefetch = True                     # the prefetch group (False means no group)

    default_export_compatible = False   # whether the field must be exported by default in an import-compatible export
    exportable = True

    def __init__(self, string: str | Sentinel = SENTINEL, **kwargs):
        kwargs['string'] = string
        self._sequence = next(_global_seq)
        self.args = self._args__ = {key: val for key, val in kwargs.items() if val is not SENTINEL}

    def __str__(self):
        if self.name is None:
            return "<%s.%s>" % (__name__, type(self).__name__)
        return "%s.%s" % (self.model_name, self.name)

    def __repr__(self):
        if self.name is None:
            return f"{'<%s.%s>'!r}" % (__name__, type(self).__name__)
        return f"{'%s.%s'!r}" % (self.model_name, self.name)
    
    ############################################################################
    #
    # Base field setup: things that do not depend on other models/fields
    #
    # The base field setup is done by field.__set_name__(), which determines the
    # field's name, model name, module and its parameters.
    #
    # The dictionary field._args__ gives the parameters passed to the field's
    # constructor.  Most parameters have an attribute of the same name on the
    # field.  The parameters as attributes are assigned by the field setup.
    #
    # When several definition classes of the same model redefine a given field,
    # the field occurrences are "merged" into one new field instantiated at
    # runtime on the registry class of the model.  The occurrences of the field
    # are given to the new field as the parameter '_base_fields'; it is a list
    # of fields in override order (or reverse MRO).
    #
    # In order to save memory, a field should avoid having field._args__ and/or
    # many attributes when possible.  We call "direct" a field that can be set
    # up directly from its definition class.  Direct fields are non-related
    # fields defined on models, and can be shared across registries.  We call
    # "toplevel" a field that is put on the model's registry class, and is
    # therefore specific to the registry.
    #
    # Toplevel field are set up once, and are no longer set up from scratch
    # after that.  Those fields can save memory by discarding field._args__ and
    # field._base_fields once set up, because those are no longer necessary.
    #
    # Non-toplevel non-direct fields are the fields on definition classes that
    # may not be shared.  In other words, those fields are never used directly,
    # and are always recreated as toplevel fields.  On those fields, the base
    # setup is useless, because only field._args__ is used for setting up other
    # fields.  We therefore skip the base setup for those fields.  The only
    # attributes of those fields are: '_sequence', '_args__', 'model_name', 'name'
    # and '_module', which makes their __dict__'s size minimal.

    def __set_name__(self, owner, name):
        """ Perform the base setup of a field.

        :param owner: the owner class of the field (the model's definition or registry class)
        :param name: the name of the field
        """
        assert issubclass(owner, BaseModel)
        self.model_name = owner._name
        self.name = name
        if is_definition_class(owner):
            # only for fields on definition classes, not registry classes
            self._module = owner._module
            owner._field_definitions.append(self)

        if not self._args__.get('related'):
            self._direct = True
        if self._direct or self._toplevel:
            self._setup_attrs(owner, name)
            if self._toplevel:
                # free memory, self._args__ and self._base_fields are no longer useful
                self.__dict__.pop('_args__', None)
                self.__dict__.pop('_base_fields', None)


class Boolean(Field[bool]):
    """ Encapsulates a :class:`bool`. """
    type = 'boolean'
    _column_type = ('bool', 'bool')

    def convert_to_column(self, value, record, values=None, validate=True):
        return bool(value)

    def convert_to_column_update(self, value, record):
        if self.company_dependent:
            value = {k: bool(v) for k, v in value.items()}
        return super().convert_to_column_update(value, record)

    def convert_to_cache(self, value, record, validate=True):
        return bool(value)

    def convert_to_export(self, value, record):
        return bool(value)


class Integer(Field[int]):
    """ Encapsulates an :class:`int`. """
    type = 'integer'
    _column_type = ('int4', 'int4')

    aggregator = 'sum'


class Selection(Field[str | typing.Literal[False]]):
    """ Encapsulates an exclusive choice between different values.

    :param selection: specifies the possible values for this field.
        It is given as either a list of pairs ``(value, label)``, or a model
        method, or a method name.
    :type selection: list(tuple(str,str)) or callable or str

    :param selection_add: provides an extension of the selection in the case
        of an overridden field. It is a list of pairs ``(value, label)`` or
        singletons ``(value,)``, where singleton values must appear in the
        overridden selection. The new values are inserted in an order that is
        consistent with the overridden selection and this list::

            selection = [('a', 'A'), ('b', 'B')]
            selection_add = [('c', 'C'), ('b',)]
            > result = [('a', 'A'), ('c', 'C'), ('b', 'B')]
    :type selection_add: list(tuple(str,str))

    :param ondelete: provides a fallback mechanism for any overridden
        field with a selection_add. It is a dict that maps every option
        from the selection_add to a fallback action.

        This fallback action will be applied to all records whose
        selection_add option maps to it.

        The actions can be any of the following:
            - 'set null' -- the default, all records with this option
              will have their selection value set to False.
            - 'cascade' -- all records with this option will be
              deleted along with the option itself.
            - 'set default' -- all records with this option will be
              set to the default of the field definition
            - 'set VALUE' -- all records with this option will be
              set to the given value
            - <callable> -- a callable whose first and only argument will be
              the set of records containing the specified Selection option,
              for custom processing

    The attribute ``selection`` is mandatory except in the case of
    ``related`` or extended fields.
    """
    type = 'selection'
    _column_type = ('varchar', pg_varchar())

    selection = None            # [(value, string), ...], function or method name
    validate = True             # whether validating upon write
    ondelete = None             # {value: policy} (what to do when value is deleted)

    def __init__(self, selection=SENTINEL, string: str | Sentinel = SENTINEL, **kwargs):
        super(Selection, self).__init__(selection=selection, string=string, **kwargs)
        self._selection = dict(selection) if isinstance(selection, list) else None


class _String(Field[str | typing.Literal[False]]):
    """ Abstract class for string fields. """
    translate = False                   # whether the field is translated
    size = None                         # maximum size of values (deprecated)

    def __init__(self, string: str | Sentinel = SENTINEL, **kwargs):
        # translate is either True, False, or a callable
        if 'translate' in kwargs and not callable(kwargs['translate']):
            kwargs['translate'] = bool(kwargs['translate'])
        super().__init__(string=string, **kwargs)

class Char(_String):
    """ Basic string field, can be length-limited, usually displayed as a
        single-line string in clients.

        :param int size: the maximum size of values stored for that field

        :param bool trim: states whether the value is trimmed or not (by default,
            ``True``). Note that the trim operation is applied only by the web client.

        :param translate: enable the translation of the field's values; use
            ``translate=True`` to translate field values as a whole; ``translate``
            may also be a callable such that ``translate(callback, value)``
            translates ``value`` by using ``callback(term)`` to retrieve the
            translation of terms.
        :type translate: bool or callable
    """
    type = 'char'
    trim = True                         # whether value is trimmed (only by web client)


class Datetime(Field[datetime | typing.Literal[False]]):
    """ Encapsulates a python :class:`datetime <datetime.datetime>` object. """
    type = 'datetime'
    _column_type = ('timestamp', 'timestamp')

    start_of = staticmethod(date_utils.start_of)
    end_of = staticmethod(date_utils.end_of)
    add = staticmethod(date_utils.add)
    subtract = staticmethod(date_utils.subtract)

    @staticmethod
    def now(*args):
        """Return the current day and time in the format expected by the ORM.

        .. note:: This function may be used to compute default values.
        """
        # microseconds must be annihilated as they don't comply with the server datetime format
        return datetime.now().replace(microsecond=0)


class _Relational(Field[M], typing.Generic[M]):
    """ Abstract class for relational fields. """
    relational = True
    domain: DomainType = []         # domain for searching values
    context: ContextType = {}       # context for searching values
    check_company = False

    def __get__(self, records, owner=None):
        # base case: do the regular access
        if records is None or len(records._ids) <= 1:
            return super().__get__(records, owner)
        # multirecord case: use mapped
        return self.mapped(records)

    _related_context = property(attrgetter('context'))

    _description_relation = property(attrgetter('comodel_name'))
    _description_context = property(attrgetter('context'))

class Many2one(_Relational[M]):
    """ The value of such a field is a recordset of size 0 (no
    record) or 1 (a single record).

    :param str comodel_name: name of the target model
        ``Mandatory`` except for related or extended fields.

    :param domain: an optional domain to set on candidate values on the
        client side (domain or a python expression that will be evaluated
        to provide domain)

    :param dict context: an optional context to use on the client side when
        handling that field

    :param str ondelete: what to do when the referred record is deleted;
        possible values are: ``'set null'``, ``'restrict'``, ``'cascade'``

    :param bool auto_join: whether JOINs are generated upon search through that
        field (default: ``False``)

    :param bool delegate: set it to ``True`` to make fields of the target model
        accessible from the current model (corresponds to ``_inherits``)

    :param bool check_company: Mark the field to be verified in
        :meth:`~odoo.models.Model._check_company`. Has a different behaviour
        depending on whether the field is company_dependent or not.
        Constrains non-company-dependent fields to target records whose
        company_id(s) are compatible with the record's company_id(s).
        Constrains company_dependent fields to target records whose
        company_id(s) are compatible with the currently active company.
    """
    type = 'many2one'
    _column_type = ('int4', 'int4')

    ondelete = None                     # what to do when value is deleted
    auto_join = False                   # whether joins are generated upon search
    delegate = False                    # whether self implements delegation

    def __init__(self, comodel_name: str | Sentinel = SENTINEL, string: str | Sentinel = SENTINEL, **kwargs):
        super().__init__(comodel_name=comodel_name, string=string, **kwargs)


class Id(Field[IdType | typing.Literal[False]]):
    """ Special case for field 'id'. """
    type = 'integer'
    column_type = ('int4', 'int4')

    string = 'ID'
    store = True
    readonly = True
    prefetch = False

    def update_db(self, model, columns):
        pass                            # this column is created with the table

    def __get__(self, record, owner=None):
        if record is None:
            return self         # the field is accessed through the class owner

        # the code below is written to make record.id as quick as possible
        ids = record._ids
        size = len(ids)
        if size == 0:
            return False
        elif size == 1:
            return ids[0]
        raise ValueError("Expected singleton: %s" % record)

    def __set__(self, record, value):
        raise TypeError("field 'id' cannot be assigned")