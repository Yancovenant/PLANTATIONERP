# -*- coding: utf-8 -*-
# Part of Inphms, see License file for full copyright and licensing details.

"""Models Registry
"""
from __future__ import annotations

import inspect
import logging
import os
import threading
import time
import typing
import warnings
from collections import defaultdict, deque
from collections.abc import Mapping
from contextlib import closing, contextmanager, nullcontext
from functools import partial
from operator import attrgetter

import psycopg2

import inphms
# from inphms.modules.db import FunctionStatus
from .. import SUPERUSER_ID
# from inphms.sql_db import TestCursor
from inphms.tools import (
    config, lazy_classproperty, SQL,
    lazy_property, OrderedSet, remove_accents,
    # , sql, , ,
    # ,
)
from inphms.tools.func import locked
from inphms.tools.lru import LRU
# from inphms.tools.misc import Collector, format_frame

if typing.TYPE_CHECKING:
    from inphms.models import BaseModel

_logger = logging.getLogger(__name__)
_schema = logging.getLogger('inphms.schema')

_REGISTRY_CACHES = {
    'default': 8192,
    'assets': 512, # arbitrary
    'templates': 1024, # arbitrary
    'routing': 1024,  # 2 entries per website
    'routing.rewrites': 8192,  # url_rewrite entries
    'templates.cached_values': 2048, # arbitrary
    'groups': 1,  # contains all res.groups
}

# cache invalidation dependencies, as follows:
# { 'cache_key': ('cache_container_1', 'cache_container_3', ...) }
_CACHES_BY_KEY = {
    'default': ('default', 'templates.cached_values'),
    'assets': ('assets', 'templates.cached_values'),
    'templates': ('templates', 'templates.cached_values'),
    'routing': ('routing', 'routing.rewrites', 'templates.cached_values'),
    'groups': ('groups', 'templates', 'templates.cached_values'),  # The processing of groups is saved in the view
}

_REPLICA_RETRY_TIME = 20 * 60  # 20 minutes


def _unaccent(x):
    if isinstance(x, SQL):
        return SQL("unaccent(%s)", x)
    if isinstance(x, psycopg2.sql.Composable):
        return psycopg2.sql.SQL('unaccent({})').format(x)
    return f'unaccent({x})'


class Registry(Mapping):
    """ Model registry for a particular database.

    The registry is essentially a mapping between model names and model classes.
    There is one registry instance per database.

    """
    _lock = threading.RLock()
    _saved_lock = None

    @lazy_classproperty
    def registries(cls):
        """ A mapping from database names to registries. """
        size = config.get('registry_lru_size', None)
        if not size:
            # Size the LRU depending of the memory limits
            if os.name != 'posix':
                # cannot specify the memory limit soft on windows...
                size = 42
            else:
                # A registry takes 10MB of memory on average, so we reserve
                # 10Mb (registry) + 5Mb (working memory) per registry
                avgsz = 15 * 1024 * 1024
                size = int(config['limit_memory_soft'] / avgsz)
        return LRU(size)
    
    def __new__(cls, db_name):
        """ Return the registry for the given database name."""
        assert db_name, "Missing database name"
        with cls._lock:
            try:
                return cls.registries[db_name]
            except KeyError:
                return cls.new(db_name)
    
    
    def init(self, db_name):
        print("init registry", db_name)
        self.models: dict[str, type[BaseModel]] = {}    # model name/model instance mapping
        self._sql_constraints = set()
        self._init = True
        self._database_translated_fields = ()  # names of translated fields in database
        self._database_company_dependent_fields = ()  # names of company dependent fields in database
        if config['test_enable'] or config['test_file']:
            from inphms.tests.result import InphmsTestResult  # noqa: PLC0415
            self._assertion_report = InphmsTestResult()
        else:
            self._assertion_report = None
        self._fields_by_model = None
        self._ordinary_tables = None
        self._constraint_queue = deque()
        self.__caches = {cache_name: LRU(cache_size) for cache_name, cache_size in _REGISTRY_CACHES.items()}

        # modules fully loaded (maintained during init phase by `loading` module)
        self._init_modules = set()
        self.updated_modules = []       # installed/updated modules
        self.loaded_xmlids = set()

        self.db_name = db_name
        self._db = inphms.sql_db.db_connect(db_name, readonly=False)
        self._db_readonly = None
        self._db_readonly_failed_time = None
        if config['db_replica_host'] is not False or config['test_enable']:  # by default, only use readonly pool if we have a db_replica_host defined. Allows to have an empty replica host for testing
            self._db_readonly = inphms.sql_db.db_connect(db_name, readonly=True)

        # cursor for test mode; None means "normal" mode
        self.test_cr = None
        self.test_lock = None

        # Indicates that the registry is
        self.loaded = False             # whether all modules are loaded
        self.ready = False              # whether everything is set up

        # field dependencies
        self.field_depends = Collector()
        self.field_depends_context = Collector()
        self.field_inverses = Collector()

        # company dependent
        self.many2one_company_dependents = Collector()  # {model_name: (field1, field2, ...)}

        # cache of methods get_field_trigger_tree() and is_modifying_relations()
        self._field_trigger_trees = {}
        self._is_modifying_relations = {}

        # Inter-process signaling:
        # The `base_registry_signaling` sequence indicates the whole registry
        # must be reloaded.
        # The `base_cache_signaling sequence` indicates all caches must be
        # invalidated (i.e. cleared).
        self.registry_sequence = None
        self.cache_sequences = {}

        # Flags indicating invalidation of the registry or the cache.
        self._invalidation_flags = threading.local()

        with closing(self.cursor()) as cr:
            self.has_unaccent = inphms.modules.db.has_unaccent(cr)
            self.has_trigram = inphms.modules.db.has_trigram(cr)

        self.unaccent = _unaccent if self.has_unaccent else lambda x: x
        self.unaccent_python = remove_accents if self.has_unaccent else lambda x: x

    @classmethod
    @locked
    def new(cls, db_name, force_demo=False, status=None, update_module=False):
        """ Create and return a new registry for the given database name. """
        print("new registry", db_name, force_demo, status, update_module)
        t0 = time.time()
        registry = object.__new__(cls)
        registry.init(db_name)
        registry.new = registry.init = registry.registries = None

        # Initializing a registry will call general code which will in
        # turn call Registry() to obtain the registry being initialized.
        # Make it available in the registries dictionary then remove it
        # if an exception is raised.
        cls.delete(db_name)
        cls.registries[db_name] = registry  # pylint: disable=unsupported-assignment-operation
        try:
            registry.setup_signaling()
            # This should be a method on Registry
            try:
                inphms.modules.load_modules(registry, force_demo, status, update_module)
            except Exception:
                inphms.modules.reset_modules_state(db_name)
                raise
        except Exception:
            _logger.error('Failed to load registry')
            del cls.registries[db_name]     # pylint: disable=unsupported-delete-operation
            raise

        # load_modules() above can replace the registry by calling
        # indirectly new() again (when modules have to be uninstalled).
        # Yeah, crazy.
        registry = cls.registries[db_name]  # pylint: disable=unsubscriptable-object

        registry._init = False
        registry.ready = True
        registry.registry_invalidated = bool(update_module)
        registry.signal_changes()

        _logger.info("Registry loaded in %.3fs", time.time() - t0)
        return registry
    
    #
    # Mapping abstract methods implementation
    # => mixin provides methods keys, items, values, get, __eq__, and __ne__
    #
    def __len__(self):
        """ Return the size of the registry. """
        return len(self.models)

    def __iter__(self):
        """ Return an iterator over all model names. """
        return iter(self.models)

    def __getitem__(self, model_name: str) -> type[BaseModel]:
        """ Return the model with the given name or raise KeyError if it doesn't exist."""
        return self.models[model_name]

    def __call__(self, model_name):
        """ Same as ``self[model_name]``. """
        return self.models[model_name]

    def __setitem__(self, model_name, model):
        """ Add or replace a model in the registry."""
        self.models[model_name] = model

    def __delitem__(self, model_name):
        """ Remove a (custom) model from the registry. """
        del self.models[model_name]
        # the custom model can inherit from mixins ('mail.thread', ...)
        for Model in self.models.values():
            Model._inherit_children.discard(model_name)