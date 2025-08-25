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
    lazy_property,
    # lazy_property, sql, OrderedSet, SQL,
    # remove_accents,
)
from inphms.tools.func import locked
from inphms.tools.lru import LRU
# from inphms.tools.misc import Collector, format_frame

# if typing.TYPE_CHECKING:
    # from inphms.models import BaseModel

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
    
    @classmethod
    @locked
    def new(cls, db_name, force_demo=False, status=None, update_module=False):
        """ Create and return a new registry for the given database name. """
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