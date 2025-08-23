# Part of Inphms, see License file for full copyright and licensing details.

"""
The PostgreSQL connector is a connectivity layer between the Inphms code and
the database, *not* a database abstraction toolkit. Database abstraction is what
the ORM does, in fact.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
import typing
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from inspect import currentframe

import psycopg2
import psycopg2.extensions
import psycopg2.extras
from psycopg2.extensions import ISOLATION_LEVEL_REPEATABLE_READ
from psycopg2.pool import PoolError
from psycopg2.sql import Composable
from werkzeug import urls

import inphms
from . import tools
from .tools.func import locked
from .tools.misc import Callbacks

if typing.TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    T = typing.TypeVar('T')

def undecimalize(value, cr):
    if value is None:
        return None
    return float(value)

DECIMAL_TO_FLOAT_TYPE = psycopg2.extensions.new_type((1700,), 'float', undecimalize)
psycopg2.extensions.register_type(DECIMAL_TO_FLOAT_TYPE)
psycopg2.extensions.register_type(psycopg2.extensions.new_array_type((1231,), 'float[]', DECIMAL_TO_FLOAT_TYPE))

_logger = logging.getLogger(__name__)
_logger_conn = _logger.getChild("connection")

real_time = time.time.__call__  # ensure we have a non patched time for query times when using freezegun

re_from = re.compile(r'\bfrom\s+"?([a-zA-Z_0-9]+)\b', re.IGNORECASE)
re_into = re.compile(r'\binto\s+"?([a-zA-Z_0-9]+)\b', re.IGNORECASE)

def categorize_query(decoded_query):
    res_into = re_into.search(decoded_query)
    # prioritize `insert` over `select` so `select` subqueries are not
    # considered when inside a `insert`
    if res_into:
        return 'into', res_into.group(1)

    res_from = re_from.search(decoded_query)
    if res_from:
        return 'from', res_from.group(1)

    return 'other', None


sql_counter = 0

MAX_IDLE_TIMEOUT = 60 * 10

def connection_info_for(db_or_uri, readonly=False):
    """ parse the given `db_or_uri` and return a 2-tuple (dbname, connection_params)

    Connection params are either a dictionary with a single key ``dsn``
    containing a connection URI, or a dictionary containing connection
    parameter keywords which psycopg2 can build a key/value connection string
    (dsn) from

    :param str db_or_uri: database name or postgres dsn
    :param bool readonly: used to load
        the default configuration from ``db_`` or ``db_replica_``.
    :rtype: (str, dict)
    """
    if 'INPHMS_PGAPPNAME' in os.environ:
        # Using manual string interpolation for security reason and trimming at default NAMEDATALEN=63
        app_name = os.environ['INPHMS_PGAPPNAME'].replace('{pid}', str(os.getpid()))[0:63]
    else:
        app_name = "inphms-%d" % os.getpid()
    if db_or_uri.startswith(('postgresql://', 'postgres://')):
        # extract db from uri
        us = urls.url_parse(db_or_uri)
        if len(us.path) > 1:
            db_name = us.path[1:]
        elif us.username:
            db_name = us.username
        else:
            db_name = us.hostname
        return db_name, {'dsn': db_or_uri, 'application_name': app_name}

    connection_info = {'database': db_or_uri, 'application_name': app_name}
    for p in ('host', 'port', 'user', 'password', 'sslmode'):
        cfg = tools.config['db_' + p]
        if readonly:
            cfg = tools.config.get('db_replica_' + p, cfg)
        if cfg:
            connection_info[p] = cfg
    print(connection_info, 'connection_info')
    print(db_or_uri, 'db_or_uri')
    print(tools.config['db_name'], 'tools.config')
    return db_or_uri, connection_info

_Pool = None
_Pool_readonly = None

def db_connect(to, allow_uri=False, readonly=False):
    global _Pool, _Pool_readonly  # noqa: PLW0603 (global-statement)

    maxconn = inphms.evented and tools.config['db_maxconn_gevent'] or tools.config['db_maxconn']
    if _Pool is None and not readonly:
        _Pool = ConnectionPool(int(maxconn), readonly=False)
    if _Pool_readonly is None and readonly:
        _Pool_readonly = ConnectionPool(int(maxconn), readonly=True)

    db, info = connection_info_for(to, readonly)
    if not allow_uri and db != to:
        raise ValueError('URI connections not allowed')
    return Connection(_Pool_readonly if readonly else _Pool, db, info)

def close_all():
    if _Pool:
        _Pool.close_all()
    if _Pool_readonly:
        _Pool_readonly.close_all()

class ConnectionPool(object):
    """ The pool of connections to database(s)

        Keep a set of connections to pg databases open, and reuse them
        to open cursors for all transactions.

        The connections are *not* automatically closed. Only a close_db()
        can trigger that.
    """
    def __init__(self, maxconn=64, readonly=False):
        self._connections = []
        self._maxconn = max(maxconn, 1)
        self._readonly = readonly
        self._lock = threading.Lock()
    
    def __repr__(self):
        used = len([1 for c, u, _ in self._connections[:] if u])
        count = len(self._connections)
        mode = 'read-only' if self._readonly else 'read/write'
        return f"ConnectionPool({mode};used={used}/count={count}/max={self._maxconn})"

    @property
    def readonly(self):
        return self._readonly

    def _debug(self, msg, *args):
        _logger_conn.debug(('%r ' + msg), self, *args)
    
    @locked
    def borrow(self, connection_info):
        """
        Borrow a PsycoConnection from the pool. If no connection is available, create a new one
        as long as there are still slots available. Perform some garbage-collection in the pool:
        idle, dead and leaked connections are removed.

        :param dict connection_info: dict of psql connection keywords
        :rtype: PsycoConnection
        """
        # free idle, dead and leaked connections
        for i, (cnx, used, last_used) in tools.reverse_enumerate(self._connections):
            if not used and not cnx.closed and time.time() - last_used > MAX_IDLE_TIMEOUT:
                self._debug('Close connection at index %d: %r', i, cnx.dsn)
                cnx.close()
            if cnx.closed:
                self._connections.pop(i)
                self._debug('Removing closed connection at index %d: %r', i, cnx.dsn)
                continue
            if getattr(cnx, 'leaked', False):
                delattr(cnx, 'leaked')
                self._connections[i][1] = False
                _logger.info('%r: Free leaked connection to %r', self, cnx.dsn)

        for i, (cnx, used, _) in enumerate(self._connections):
            if not used and self._dsn_equals(cnx.dsn, connection_info):
                try:
                    cnx.reset()
                except psycopg2.OperationalError:
                    self._debug('Cannot reset connection at index %d: %r', i, cnx.dsn)
                    # psycopg2 2.4.4 and earlier do not allow closing a closed connection
                    if not cnx.closed:
                        cnx.close()
                    continue
                self._connections[i][1] = True
                self._debug('Borrow existing connection to %r at index %d', cnx.dsn, i)

                return cnx

        if len(self._connections) >= self._maxconn:
            # try to remove the oldest connection not used
            for i, (cnx, used, _) in enumerate(self._connections):
                if not used:
                    self._connections.pop(i)
                    if not cnx.closed:
                        cnx.close()
                    self._debug('Removing old connection at index %d: %r', i, cnx.dsn)
                    break
            else:
                # note: this code is called only if the for loop has completed (no break)
                raise PoolError('The Connection Pool Is Full')

        try:
            result = psycopg2.connect(
                connection_factory=PsycoConnection,
                **connection_info)
        except psycopg2.Error:
            _logger.info('Connection to the database failed')
            raise
        self._connections.append([result, True, 0])
        self._debug('Create new connection backend PID %d', result.get_backend_pid())

        return result
    
    @locked
    def close_all(self, dsn=None):
        count = 0
        last = None
        for i, (cnx, _, _) in tools.reverse_enumerate(self._connections):
            if dsn is None or self._dsn_equals(cnx.dsn, dsn):
                cnx.close()
                last = self._connections.pop(i)[0]
                count += 1
        if count:
            _logger.info('%r: Closed %d connections %s', self, count,
                        (dsn and last and 'to %r' % last.dsn) or '')

class Connection(object):
    """ A lightweight instance of a connection to postgres
    """
    def __init__(self, pool, dbname, dsn):
        self.__dbname = dbname
        self.__dsn = dsn
        self.__pool = pool
    
    @property
    def dsn(self):
        dsn = dict(self.__dsn)
        dsn.pop('password', None)
        return dsn

    @property
    def dbname(self):
        return self.__dbname
    
    def cursor(self):
        _logger.debug('create cursor to %r', self.dsn)
        return Cursor(self.__pool, self.__dbname, self.__dsn)

    def __bool__(self):
        raise NotImplementedError()
    

class BaseCursor:
    """ Base class for cursors that manage pre/post commit hooks. """
    def __init__(self):
        self.precommit = Callbacks()
        self.postcommit = Callbacks()
        self.prerollback = Callbacks()
        self.postrollback = Callbacks()
        # By default a cursor has no transaction object.  A transaction object
        # for managing environments is instantiated by registry.cursor().  It
        # is not done here in order to avoid cyclic module dependencies.
        self.transaction = None
    
    def __enter__(self):
        """ Using the cursor as a contextmanager automatically commits and
            closes it::

                with cr:
                    cr.execute(...)

                # cr is committed if no failure occurred
                # cr is closed in any case
        """
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            if exc_type is None:
                self.commit()
        finally:
            self.close()
    
    def flush(self):
        """ Flush the current transaction, and run precommit hooks. """
        if self.transaction is not None:
            self.transaction.flush()
        self.precommit.run()
    
    def clear(self):
        """ Clear the current transaction, and clear precommit hooks. """
        if self.transaction is not None:
            self.transaction.clear()
        self.precommit.clear()

class Cursor(BaseCursor):
    """Represents an open transaction to the PostgreSQL DB backend,
       acting as a lightweight wrapper around psycopg2's
       ``cursor`` objects.

        ``Cursor`` is the object behind the ``cr`` variable used all
        over the Inphms code.

        .. rubric:: Transaction Isolation

        One very important property of database transactions is the
        level of isolation between concurrent transactions.
        The SQL standard defines four levels of transaction isolation,
        ranging from the most strict *Serializable* level, to the least
        strict *Read Uncommitted* level. These levels are defined in
        terms of the phenomena that must not occur between concurrent
        transactions, such as *dirty read*, etc.
        In the context of a generic business data management software
        such as Inphms, we need the best guarantees that no data
        corruption can ever be cause by simply running multiple
        transactions in parallel. Therefore, the preferred level would
        be the *serializable* level, which ensures that a set of
        transactions is guaranteed to produce the same effect as
        running them one at a time in some order.

        However, most database management systems implement a limited
        serializable isolation in the form of
        `snapshot isolation <http://en.wikipedia.org/wiki/Snapshot_isolation>`_,
        providing most of the same advantages as True Serializability,
        with a fraction of the performance cost.
        With PostgreSQL up to version 9.0, this snapshot isolation was
        the implementation of both the ``REPEATABLE READ`` and
        ``SERIALIZABLE`` levels of the SQL standard.
        As of PostgreSQL 9.1, the previous snapshot isolation implementation
        was kept for ``REPEATABLE READ``, while a new ``SERIALIZABLE``
        level was introduced, providing some additional heuristics to
        detect a concurrent update by parallel transactions, and forcing
        one of them to rollback.

        Inphms implements its own level of locking protection
        for transactions that are highly likely to provoke concurrent
        updates, such as stock reservations or document sequences updates.
        Therefore we mostly care about the properties of snapshot isolation,
        but we don't really need additional heuristics to trigger transaction
        rollbacks, as we are taking care of triggering instant rollbacks
        ourselves when it matters (and we can save the additional performance
        hit of these heuristics).

        As a result of the above, we have selected ``REPEATABLE READ`` as
        the default transaction isolation level for Inphms cursors, as
        it will be mapped to the desired ``snapshot isolation`` level for
        all supported PostgreSQL version (>10).

        .. attribute:: cache

            Cache dictionary with a "request" (-ish) lifecycle, only lives as
            long as the cursor itself does and proactively cleared when the
            cursor is closed.

            This cache should *only* be used to store repeatable reads as it
            ignores rollbacks and savepoints, it should not be used to store
            *any* data which may be modified during the life of the cursor.

    """
    IN_MAX = 1000   # decent limit on size of IN queries - guideline = Oracle limit

    def __init__(self, pool, dbname, dsn):
        super().__init__()
        self.sql_from_log = {}
        self.sql_into_log = {}

        # default log level determined at cursor creation, could be
        # overridden later for debugging purposes
        self.sql_log_count = 0

        # avoid the call of close() (by __del__) if an exception
        # is raised by any of the following initializations
        self._closed = True

        self.__pool = pool
        self.dbname = dbname

        self._cnx = pool.borrow(dsn)
        self._obj = self._cnx.cursor()
        if _logger.isEnabledFor(logging.DEBUG):
            self.__caller = frame_codeinfo(currentframe(), 2)
        else:
            self.__caller = False
        self._closed = False   # real initialization value
        # See the docstring of this class.
        self.connection.set_isolation_level(ISOLATION_LEVEL_REPEATABLE_READ)
        self.connection.set_session(readonly=pool.readonly)

        self.cache = {}
        self._now = None
        if os.getenv('INPHMS_FAKETIME_TEST_MODE') and self.dbname in tools.config['db_name'].split(','):
            self.execute("SET search_path = public, pg_catalog;")
            self.commit()  # ensure that the search_path remains after a rollback

    def __build_dict(self, row):
        return {d.name: row[i] for i, d in enumerate(self._obj.description)}
    
    def __getattr__(self, name):
        if self._closed and name == '_obj':
            raise psycopg2.InterfaceError("Cursor already closed")
        return getattr(self._obj, name)
    
    def commit(self):
        """ Perform an SQL `COMMIT` """
        self.flush()
        result = self._cnx.commit()
        self.clear()
        self._now = None
        self.prerollback.clear()
        self.postrollback.clear()
        self.postcommit.run()
        return result
    
    

class PsycoConnection(psycopg2.extensions.connection):
    def lobject(*args, **kwargs):
        pass

    if hasattr(psycopg2.extensions, 'ConnectionInfo'):
        @property
        def info(self):
            class PsycoConnectionInfo(psycopg2.extensions.ConnectionInfo):
                @property
                def password(self):
                    pass
            return PsycoConnectionInfo(self)