# Part of Inphms, see License file for full copyright and licensing details.
r"""\
Inphms HTTP layer / WSGI application

The main duty of this module is to prepare and dispatch all http
requests to their corresponding controllers: from a raw http request
arriving on the WSGI entrypoint to a :class:`~http.Request`: arriving at
a module controller with a fully setup ORM available.

Application developers mostly know this module thanks to the
:class:`~inphms.http.Controller`: class and its companion the
:func:`~inphms.http.route`: method decorator. Together they are used to
register methods responsible of delivering web content to matching URLS.

Those two are only the tip of the iceberg, below is a call graph that
shows the various processing layers each request passes through before
ending at the @route decorated endpoint. Hopefully, this call graph and
the attached function descriptions will help you understand this module.

Here be dragons:

"""

import base64
import collections
import collections.abc
import contextlib
import functools
import glob
import hashlib
import hmac
import importlib.metadata
import inspect
import json
import logging
import mimetypes
import os
import re
import threading
import time
import traceback
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from hashlib import sha512
from io import BytesIO
from os.path import join as opj
from pathlib import Path
from urllib.parse import urlparse
from zlib import adler32

import babel.core

try:
    import geoip2.database
    import geoip2.models
    import geoip2.errors
except ImportError:
    geoip2 = None

try:
    import maxminddb
except ImportError:
    maxminddb = None

import psycopg2
import werkzeug.datastructures
import werkzeug.exceptions
import werkzeug.local
import werkzeug.routing
import werkzeug.security
import werkzeug.wrappers
import werkzeug.wsgi
from werkzeug.urls import URL, url_parse, url_encode, url_quote
from werkzeug.exceptions import (HTTPException, BadRequest, Forbidden,
                                 NotFound, InternalServerError)
try:
    from werkzeug.middleware.proxy_fix import ProxyFix as ProxyFix_
    ProxyFix = functools.partial(ProxyFix_, x_for=1, x_proto=1, x_host=1)
except ImportError:
    from werkzeug.contrib.fixers import ProxyFix

try:
    from werkzeug.utils import send_file as _send_file
except ImportError:
    from .tools._vendor.send_file import send_file as _send_file

import inphms
from .tools import (
    parse_version, config,
)
from .tools._vendor import sessions
from .tools._vendor.useragents import UserAgent
from .exceptions import UserError, AccessError, AccessDenied
from .tools.func import lazy_property, filter_kwargs
from .tools.facade import Proxy, ProxyFunc, ProxyAttr


_logger = logging.getLogger(__name__)

# =========================================================
# Const
# =========================================================

# The validity duration of a preflight response, one day.
CORS_MAX_AGE = 60 * 60 * 24

# The HTTP methods that do not require a CSRF validation.
CSRF_FREE_METHODS = ('GET', 'HEAD', 'OPTIONS', 'TRACE')

# The default csrf token lifetime, a salt against BREACH, one year
CSRF_TOKEN_SALT = 60 * 60 * 24 * 365

# The default lang to use when the browser doesn't specify it
DEFAULT_LANG = 'en_US'

# The dictionary to initialise a new session with.
def get_default_session():
    return {
        'context': {},  # 'lang': request.default_lang()  # must be set at runtime
        'db': None,
        'debug': '',
        'login': None,
        'uid': None,
        'session_token': None,
        '_trace': [],
    }

DEFAULT_MAX_CONTENT_LENGTH = 128 * 1024 * 1024  # 128MiB

# Two empty objects used when the geolocalization failed. They have the
# sames attributes as real countries/cities except that accessing them
# evaluates to None.
if geoip2:
    GEOIP_EMPTY_COUNTRY = geoip2.models.Country({})
    GEOIP_EMPTY_CITY = geoip2.models.City({})

# The request mimetypes that transport JSON in their body.
JSON_MIMETYPES = ('application/json', 'application/json-rpc')

MISSING_CSRF_WARNING = """\
No CSRF validation token provided for path %r

Inphms URLs are CSRF-protected by default (when accessed with unsafe
HTTP methods). See
https://www.inphms.com/documentation/master/developer/reference/addons/http.html#csrf
for more details.

* if this endpoint is accessed through Inphms via py-QWeb form, embed a CSRF
  token in the form, Tokens are available via `request.csrf_token()`
  can be provided through a hidden input and must be POST-ed named
  `csrf_token` e.g. in your form add:
      <input type="hidden" name="csrf_token" t-att-value="request.csrf_token()"/>

* if the form is generated or posted in javascript, the token value is
  available as `csrf_token` on `web.core` and as the `csrf_token`
  value in the default js-qweb execution context

* if the form is accessed by an external third party (e.g. REST API
  endpoint, payment gateway callback) you will need to disable CSRF
  protection (and implement your own protection if necessary) by
  passing the `csrf=False` parameter to the `route` decorator.
"""

# The @route arguments to propagate from the decorated method to the
# routing rule.
ROUTING_KEYS = {
    'defaults', 'subdomain', 'build_only', 'strict_slashes', 'redirect_to',
    'alias', 'host', 'methods',
}

if parse_version(importlib.metadata.version('werkzeug')) >= parse_version('2.0.2'):
    # Werkzeug 2.0.2 adds the websocket option. If a websocket request
    # (ws/wss) is trying to access an HTTP route, a WebsocketMismatch
    # exception is raised. On the other hand, Werkzeug 0.16 does not
    # support the websocket routing key. In order to bypass this issue,
    # let's add the websocket key only when appropriate.
    ROUTING_KEYS.add('websocket')

# The default duration of a user session cookie. Inactive sessions are reaped
# server-side as well with a threshold that can be set via an optional
# config parameter `sessions.max_inactivity_seconds` (default: SESSION_LIFETIME)
SESSION_LIFETIME = 60 * 60 * 24 * 7

# The cache duration for static content from the filesystem, one week.
STATIC_CACHE = 60 * 60 * 24 * 7

# The cache duration for content where the url uniquely identifies the
# content (usually using a hash), one year.
STATIC_CACHE_LONG = 60 * 60 * 24 * 365

# =========================================================
# Helpers
# =========================================================

class RegistryError(RuntimeError):
    pass


class SessionExpiredException(Exception):
    pass


def content_disposition(filename, disposition_type='attachment'):
    """
    Craft a ``Content-Disposition`` header, see :rfc:`6266`.

    :param filename: The name of the file, should that file be saved on
        disk by the browser.
    :param disposition_type: Tell the browser what to do with the file,
        either ``"attachment"`` to save the file on disk,
        either ``"inline"`` to display the file.
    """
    if disposition_type not in ('attachment', 'inline'):
        e = f"Invalid disposition_type: {disposition_type!r}"
        raise ValueError(e)
    return "{}; filename*=UTF-8''{}".format(
        disposition_type,
        url_quote(filename, safe='', unsafe='()<>@,;:"/[]?={}\\*\'%') # RFC6266
    )


def db_list(force=False, host=None):
    """
    Get the list of available databases.

    :param bool force: See :func:`~inphms.service.db.list_dbs`:
    :param host: The Host used to replace %h and %d in the dbfilters
        regexp. Taken from the current request when omitted.
    :returns: the list of available databases
    :rtype: List[str]
    """
    try:
        dbs = inphms.service.db.list_dbs(force)
    except psycopg2.OperationalError:
        return []
    return db_filter(dbs, host)

def db_filter(dbs, host=None):
    """
    Return the subset of ``dbs`` that match the dbfilter or the dbname
    server configuration. In case neither are configured, return ``dbs``
    as-is.

    :param Iterable[str] dbs: The list of database names to filter.
    :param host: The Host used to replace %h and %d in the dbfilters
        regexp. Taken from the current request when omitted.
    :returns: The original list filtered.
    :rtype: List[str]
    """

    if config['dbfilter']:
        #        host
        #     -----------
        # www.example.com:80
        #     -------
        #     domain
        if host is None:
            host = request.httprequest.environ.get('HTTP_HOST', '')
        host = host.partition(':')[0]
        if host.startswith('www.'):
            host = host[4:]
        domain = host.partition('.')[0]

        dbfilter_re = re.compile(
            config["dbfilter"].replace("%h", re.escape(host))
                              .replace("%d", re.escape(domain)))
        return [db for db in dbs if dbfilter_re.match(db)]

    if config['db_name']:
        # In case --db-filter is not provided and --database is passed, Inphms will
        # use the value of --database as a comma separated list of exposed databases.
        exposed_dbs = {db.strip() for db in config['db_name'].split(',')}
        return sorted(exposed_dbs.intersection(dbs))

    return list(dbs)

# =========================================================
# Session
# =========================================================

_base64_urlsafe_re = re.compile(r'^[A-Za-z0-9_-]{84}$')
_session_identifier_re = re.compile(r'^[A-Za-z0-9_-]{42}$')


class FilesystemSessionStore(sessions.FilesystemSessionStore):
    """ Place where to load and save session objects. """
    def is_valid_key(self, key): #ichecked
        return _base64_urlsafe_re.match(key) is not None

    def generate_key(self, salt=None):
        # The generated key is case sensitive (base64) and the length is 84 chars.
        # In the worst-case scenario, i.e. in an insensitive filesystem (NTFS for example)
        # taking into account the proportion of characters in the pool and a length
        # of 42 (stored part in the database), the entropy for the base64 generated key
        # is 217.875 bits which is better than the 160 bits entropy of a hexadecimal key
        # with a length of 40 (method ``generate_key`` of ``SessionStore``).
        # The risk of collision is negligible in practice.
        # Formulas:
        #   - L: length of generated word
        #   - p_char: probability of obtaining the character in the pool
        #   - n: size of the pool
        #   - k: number of generated word
        #   Entropy = - L * sum(p_char * log2(p_char))
        #   Collision ~= (1 - exp((-k * (k - 1)) / (2 * (n**L))))
        # Example:
        #   - L = 42
        #   - n = 64 // base64 has 64 possible characthers, so the size is 64
        #   - p_char = 1/64 // 1/64 is the probability of getting a specific character in the n.
        #
        #       Entropy = - L * (n * (p_char * math.log2(p_char)))
        #       print(f"Entropy bits: {Entropy}") ## Output 252 bits.
        # 
        #   - k = 1000000 // 1 million keys has been generated.
        #       Collision = (1 - math.exp((-k * (k - 1)) / (2 * (n**L))))
        #       print(f"Collision probability: {Collision}") ## Output 0.0 // negligible to happen.
        #
        # Note: im not sure why the documentation above said it was 217.875 bits,
        #       but my calculation is 252 bits. <- higher value entropy.
        # 
        key = str(time.time()).encode() + os.urandom(64)
        hash_key = sha512(key).digest()[:-1]  # prevent base64 padding
        return base64.urlsafe_b64encode(hash_key).decode('utf-8')

    def get(self, sid):
        # retro compatibility
        old_path = super().get_session_filename(sid)
        session_path = self.get_session_filename(sid)
        if os.path.isfile(old_path) and not os.path.isfile(session_path):
            dirname = os.path.dirname(session_path)
            if not os.path.isdir(dirname):
                with contextlib.suppress(OSError):
                    os.mkdir(dirname, 0o0755)
            with contextlib.suppress(OSError):
                os.rename(old_path, session_path)
        return super().get(sid)

    def get_session_filename(self, sid):
        # scatter sessions across 4096 (64^2) directories
        if not self.is_valid_key(sid):
            raise ValueError(f'Invalid session id {sid!r}')
        sha_dir = sid[:2]
        dirname = os.path.join(self.path, sha_dir)
        session_path = os.path.join(dirname, sid)
        return session_path

class Session(collections.abc.MutableMapping):
    """ Structure containing data persisted across requests. """
    __slots__ = ('can_save', '_Session__data', 'is_dirty', 'is_new',
                 'should_rotate', 'sid')

    def __init__(self, data, sid, new=False): #ichecked
        self.can_save = True
        self.__data = {}
        self.update(data)
        self.is_dirty = False
        self.is_new = new
        self.should_rotate = False
        self.sid = sid

    #
    # MutableMapping implementation with DocDict-like extension
    #
    def __getitem__(self, item):
        return self.__data[item]

    def __setitem__(self, item, value): #ichecked
        value = json.loads(json.dumps(value))
        if item not in self.__data or self.__data[item] != value:
            self.is_dirty = True
        self.__data[item] = value

    def __delitem__(self, item):
        del self.__data[item]
        self.is_dirty = True

    def __len__(self):
        return len(self.__data)

    def __iter__(self):
        return iter(self.__data)

    def __getattr__(self, attr):
        return self.get(attr, None)

    def __setattr__(self, key, val):
        if key in self.__slots__:
            super().__setattr__(key, val)
        else:
            self[key] = val

    def clear(self):
        self.__data.clear()
        self.is_dirty = True

    #
    # Session methods
    #
    def authenticate(self, dbname, credential):
        """
        Authenticate the current user with the given db, login and
        credential. If successful, store the authentication parameters in
        the current session, unless multi-factor-auth (MFA) is
        activated. In that case, that last part will be done by
        :ref:`finalize`.

        .. versionchanged:: saas-15.3
           The current request is no longer updated using the user and
           context of the session when the authentication is done using
           a database different than request.db. It is up to the caller
           to open a new cursor/registry/env on the given database.
        """
        wsgienv = {
            'interactive': True,
            'base_location': request.httprequest.url_root.rstrip('/'),
            'HTTP_HOST': request.httprequest.environ['HTTP_HOST'],
            'REMOTE_ADDR': request.httprequest.environ['REMOTE_ADDR'],
        }

        registry = Registry(dbname)
        auth_info = registry['res.users'].authenticate(dbname, credential, wsgienv)
        pre_uid = auth_info['uid']

        self.uid = None
        self.pre_login = credential['login']
        self.pre_uid = pre_uid

        with registry.cursor() as cr:
            env = inphms.api.Environment(cr, pre_uid, {})

            # if 2FA is disabled we finalize immediately
            user = env['res.users'].browse(pre_uid)
            if auth_info.get('mfa') == 'skip' or not user._mfa_url():
                self.finalize(env)

        if request and request.session is self and request.db == dbname:
            request.env = inphms.api.Environment(request.env.cr, self.uid, self.context)
            request.update_context(lang=get_lang(request.env(user=pre_uid)).code)
            # request env needs to be able to access the latest changes from the auth layers
            request.env.cr.commit()

        return auth_info

    def finalize(self, env):
        """
        Finalizes a partial session, should be called on MFA validation
        to convert a partial / pre-session into a logged-in one.
        """
        login = self.pop('pre_login')
        uid = self.pop('pre_uid')

        env = env(user=uid)
        user_context = dict(env['res.users'].context_get())

        self.should_rotate = True
        self.update({
            'db': env.registry.db_name,
            'login': login,
            'uid': uid,
            'context': user_context,
            'session_token': env.user._compute_session_token(self.sid),
        })

    def logout(self, keep_db=False):
        db = self.db if keep_db else get_default_session()['db']  # None
        debug = self.debug
        self.clear()
        self.update(get_default_session(), db=db, debug=debug)
        self.context['lang'] = request.default_lang() if request else DEFAULT_LANG
        self.should_rotate = True

        if request and request.env:
            request.env['ir.http']._post_logout()

    def touch(self):
        self.is_dirty = True

    def update_trace(self, request):
        """
            :return: dict if a device log has to be inserted, ``None`` otherwise
        """
        if self._trace_disable:
            # To avoid generating useless logs, e.g. for automated technical sessions,
            # a session can be flagged with `_trace_disable`. This should never be done
            # without a proper assessment of the consequences for auditability.
            # Non-admin users have no direct or indirect way to set this flag, so it can't
            # be abused by unprivileged users. Such sessions will of course still be
            # subject to all other auditing mechanisms (server logs, web proxy logs,
            # metadata tracking on modified records, etc.)
            return

        user_agent = request.httprequest.user_agent
        platform = user_agent.platform
        browser = user_agent.browser
        ip_address = request.httprequest.remote_addr
        now = int(datetime.now().timestamp())
        for trace in self._trace:
            if trace['platform'] == platform and trace['browser'] == browser and trace['ip_address'] == ip_address:
                # If the device logs are not up to date (i.e. not updated for one hour or more)
                if bool(now - trace['last_activity'] >= 3600):
                    trace['last_activity'] = now
                    self.is_dirty = True
                    return trace
                return
        new_trace = {
            'platform': platform,
            'browser': browser,
            'ip_address': ip_address,
            'first_activity': now,
            'last_activity': now
        }
        self._trace.append(new_trace)
        self.is_dirty = True
        return new_trace

# =========================================================
# Request and Response
# =========================================================

# Thread local global request object
_request_stack = werkzeug.local.LocalStack()
request = _request_stack()

@contextlib.contextmanager
def borrow_request():
    """ Get the current request and unexpose it from the local stack. """
    req = _request_stack.pop()
    try:
        yield req
    finally:
        _request_stack.push(req)

def make_request_wrap_methods(attr):
    def getter(self):
        return getattr(self._HTTPRequest__wrapped, attr)

    def setter(self, value):
        return setattr(self._HTTPRequest__wrapped, attr, value)

    return getter, setter

class HTTPRequest: #ichecked
    def __init__(self, environ):
        httprequest = werkzeug.wrappers.Request(environ)
        httprequest.user_agent_class = UserAgent  # use vendored userAgent since it will be removed in 2.1
        httprequest.parameter_storage_class = werkzeug.datastructures.ImmutableMultiDict
        httprequest.max_content_length = DEFAULT_MAX_CONTENT_LENGTH
        httprequest.max_form_memory_size = 10 * 1024 * 1024  # 10 MB
        self._session_id__ = httprequest.cookies.get('session_id')

        self.__wrapped = httprequest
        self.__environ = self.__wrapped.environ
        self.environ = self.headers.environ = {
            key: value
            for key, value in self.__environ.items()
            if (not key.startswith(('werkzeug.', 'wsgi.', 'socket')) or key in ['wsgi.url_scheme', 'werkzeug.proxy_fix.orig'])
        }

    def __enter__(self):
        return self

HTTPREQUEST_ATTRIBUTES = [
    '__str__', '__repr__', '__exit__',
    'accept_charsets', 'accept_languages', 'accept_mimetypes', 'access_route', 'args', 'authorization', 'base_url',
    'charset', 'content_encoding', 'content_length', 'content_md5', 'content_type', 'cookies', 'data', 'date',
    'encoding_errors', 'files', 'form', 'full_path', 'get_data', 'get_json', 'headers', 'host', 'host_url', 'if_match',
    'if_modified_since', 'if_none_match', 'if_range', 'if_unmodified_since', 'is_json', 'is_secure', 'json',
    'max_content_length', 'method', 'mimetype', 'mimetype_params', 'origin', 'path', 'pragma', 'query_string', 'range',
    'referrer', 'remote_addr', 'remote_user', 'root_path', 'root_url', 'scheme', 'script_root', 'server', 'session',
    'trusted_hosts', 'url', 'url_charset', 'url_root', 'user_agent', 'values',
]
for attr in HTTPREQUEST_ATTRIBUTES:
    setattr(HTTPRequest, attr, property(*make_request_wrap_methods(attr)))

class Request:
    """
    Wrapper around the incoming HTTP request with deserialized request
    parameters, session utilities and request dispatching logic.
    """
    def __init__(self, httprequest): #ichecked
        self.httprequest = httprequest
        # self.future_response = FutureResponse()
        self.dispatcher = _dispatchers['http'](self)  # until we match
        self.params = {}  # set by the Dispatcher

        # self.geoip = GeoIP(httprequest.remote_addr)
        self.registry = None
        self.env = None

    def _post_init(self):
        self.session, self.db = self._get_session_and_dbname()
        self._post_init = None
    
    def _get_session_and_dbname(self):
        sid = self.httprequest._session_id__
        if not sid or not root.session_store.is_valid_key(sid):
            session = root.session_store.new()
        else:
            session = root.session_store.get(sid)
            session.sid = sid  # in case the session was not persisted

        for key, val in get_default_session().items():
            session.setdefault(key, val)
        if not session.context.get('lang'):
            session.context['lang'] = self.default_lang()

        dbname = None
        host = self.httprequest.environ['HTTP_HOST']
        if session.db and db_filter([session.db], host=host):
            dbname = session.db
        else:
            all_dbs = db_list(force=True, host=host)
            if len(all_dbs) == 1:
                dbname = all_dbs[0]  # monodb

        if session.db != dbname:
            if session.db:
                _logger.warning("Logged into database %r, but dbfilter rejects it; logging session out.", session.db)
                session.logout(keep_db=False)
            session.db = dbname

        session.is_dirty = False
        return session, dbname
    
    @lazy_property
    def best_lang(self):
        lang = self.httprequest.accept_languages.best
        if not lang:
            return None

        try:
            code, territory, _, _ = babel.core.parse_locale(lang, sep='-')
            if territory:
                lang = f'{code}_{territory}'
            else:
                lang = babel.core.LOCALE_ALIASES[code]
            return lang
        except (ValueError, KeyError):
            return None

    # =====================================================
    # Helpers
    # =====================================================
    
    def default_lang(self):
        """Returns default user language according to request specification

        :returns: Preferred language if specified or 'en_US'
        :rtype: str
        """
        return self.best_lang or DEFAULT_LANG

    # =====================================================
    # Routing
    # =====================================================
    def _serve_static(self):
        """ Serve a static file from the file system. """
        module, _, path = self.httprequest.path[1:].partition('/static/')
        try:
            directory = root.statics[module]
            filepath = werkzeug.security.safe_join(directory, path)
            debug = (
                'assets' in self.session.debug and
                ' wkhtmltopdf ' not in self.httprequest.user_agent.string
            )
            res = Stream.from_path(filepath, public=True).get_response(
                max_age=0 if debug else STATIC_CACHE,
                content_security_policy=None,
            )
            root.set_csp(res)
            return res
        except KeyError:
            raise NotFound(f'Module "{module}" not found.\n')
        except OSError:  # cover both missing file and invalid permissions
            raise NotFound(f'File "{path}" not found in module {module}.\n')
    
    def _serve_db(self):
        """
        Prepare the user session and load the ORM before forwarding the
        request to ``_serve_ir_http``.
        """
        cr_readwrite = None
        rule = None
        args = None
        not_found = None

        # reuse the same cursor for building+checking the registry and
        # for matching the controller endpoint
        try:
            self.registry, cr_readwrite = self._open_registry()
            threading.current_thread().dbname = self.registry.db_name
            self.env = inphms.api.Environment(cr_readwrite, self.session.uid, self.session.context)
            try:
                rule, args = self.registry['ir.http']._match(self.httprequest.path)
            except NotFound as not_found_exc:
                not_found = not_found_exc
        finally:
            if cr_readwrite is not None:
                cr_readwrite.close()

        if not_found:
            # no controller endpoint matched -> fallback or 404
            return self._transactioning(
                functools.partial(self._serve_ir_http_fallback, not_found),
                readonly=True,
            )

        # a controller endpoint matched -> dispatch it the request
        self._set_request_dispatcher(rule)
        readonly = rule.endpoint.routing['readonly']
        if callable(readonly):
            readonly = readonly(rule.endpoint.func.__self__)
        return self._transactioning(
            functools.partial(self._serve_ir_http, rule, args),
            readonly=readonly,
        )

class _Response(werkzeug.wrappers.Response):
    """
    Outgoing HTTP response with body, status, headers and qweb support.
    In addition to the :class:`werkzeug.wrappers.Response` parameters,
    this class's constructor can take the following additional
    parameters for QWeb Lazy Rendering.

    :param str template: template to render
    :param dict qcontext: Rendering context to use
    :param int uid: User id to use for the ir.ui.view render call,
        ``None`` to use the request's user (the default)

    these attributes are available as parameters on the Response object
    and can be altered at any time before rendering

    Also exposes all the attributes and methods of
    :class:`werkzeug.wrappers.Response`.
    """
    default_mimetype = 'text/html'

    def __init__(self, *args, **kw): #ichecked
        template = kw.pop('template', None)
        qcontext = kw.pop('qcontext', None)
        uid = kw.pop('uid', None)
        super().__init__(*args, **kw)
        self.set_default(template, qcontext, uid)
    
    @classmethod
    def load(cls, result, fname="<function>"): #ichecked
        """
        Convert the return value of an endpoint into a Response.

        :param result: The endpoint return value to load the Response from.
        :type result: Union[Response, werkzeug.wrappers.BaseResponse,
            werkzeug.exceptions.HTTPException, str, bytes, NoneType]
        :param str fname: The endpoint function name wherefrom the
            result emanated, used for logging.
        :returns: The created :class:`~inphms.http.Response`.
        :rtype: Response
        :raises TypeError: When ``result`` type is none of the above-
            mentioned type.
        """
        if isinstance(result, Response):
            return result

        if isinstance(result, werkzeug.exceptions.HTTPException):
            _logger.warning("%s returns an HTTPException instead of raising it.", fname)
            raise result

        if isinstance(result, werkzeug.wrappers.Response):
            response = cls.force_type(result)
            response.set_default()
            return response

        if isinstance(result, (bytes, str, type(None))):
            return Response(result)

        raise TypeError(f"{fname} returns an invalid value: {result}")
    
    def set_default(self, template=None, qcontext=None, uid=None): #ichecked
        self.template = template
        self.qcontext = qcontext or dict()
        self.qcontext['response_template'] = self.template
        self.uid = uid
    
    def render(self):
        """ Renders the Response's template, returns the result. """
        self.qcontext['request'] = request
        return request.env["ir.ui.view"]._render_template(self.template, self.qcontext)
    
    def flatten(self):
        """
        Forces the rendering of the response's template, sets the result
        as response body and unsets :attr:`.template`
        """
        if self.template:
            self.response.append(self.render())
            self.template = None

class Response(Proxy):
    _wrapped__ = _Response

    # werkzeug.wrappers.Response attributes
    __call__ = ProxyFunc()
    add_etag = ProxyFunc(None)
    age = ProxyAttr()
    autocorrect_location_header = ProxyAttr(bool)
    # cache_control = ProxyAttr(ResponseCacheControl)
    call_on_close = ProxyFunc()
    charset = ProxyAttr(str)
    content_encoding = ProxyAttr(str)
    content_length = ProxyAttr(int)
    content_location = ProxyAttr(str)
    content_md5 = ProxyAttr(str)
    content_type = ProxyAttr(str)
    data = ProxyAttr()
    default_mimetype = ProxyAttr(str)
    default_status = ProxyAttr(int)
    delete_cookie = ProxyFunc(None)
    direct_passthrough = ProxyAttr(bool)
    expires = ProxyAttr()
    force_type = ProxyFunc(lambda v: Response(v))  # noqa: PLW0108
    freeze = ProxyFunc(None)
    get_data = ProxyFunc()
    get_etag = ProxyFunc()
    get_json = ProxyFunc()
    # headers = ProxyAttr(Headers)
    is_json = ProxyAttr(bool)
    is_sequence = ProxyAttr(bool)
    is_streamed = ProxyAttr(bool)
    iter_encoded = ProxyFunc()
    json = ProxyAttr()
    last_modified = ProxyAttr()
    location = ProxyAttr(str)
    make_conditional = ProxyFunc(lambda v: Response(v))  # noqa: PLW0108
    make_sequence = ProxyFunc(None)
    max_cookie_size = ProxyAttr(int)
    mimetype = ProxyAttr(str)
    response = ProxyAttr()
    retry_after = ProxyAttr()
    set_cookie = ProxyFunc(None)
    set_data = ProxyFunc(None)
    set_etag = ProxyFunc(None)
    status = ProxyAttr(str)
    status_code = ProxyAttr(int)
    # stream = ProxyAttr(ResponseStream)

    # inphms.http._response attributes
    load = ProxyFunc()
    set_default = ProxyFunc(None)
    qcontext = ProxyAttr()
    template = ProxyAttr(str)
    is_qweb = ProxyAttr(bool)
    render = ProxyFunc()
    flatten = ProxyFunc(None)

    def __init__(self, *args, **kwargs):
        response = None
        if len(args) == 1:
            arg = args[0]
            if isinstance(arg, Response):
                response = arg._wrapped__
            elif isinstance(arg, _Response):
                response = arg
            elif isinstance(arg, werkzeug.wrappers.Response):
                response = _Response.load(arg)
        if response is None:
            response = _Response(*args, **kwargs)

        super().__init__(response)
        if 'set_cookie' in response.__dict__:
            self.__dict__['set_cookie'] = response.__dict__['set_cookie']

# =========================================================
# Core type-specialized dispatchers
# =========================================================

_dispatchers = {}
class Dispatcher(ABC): # ABC - Abstract Base Class
    routing_type: str

    @classmethod
    def __init_subclass__(cls):
        super().__init_subclass__()
        _dispatchers[cls.routing_type] = cls

    def __init__(self, request):
        self.request = request

    @classmethod
    @abstractmethod
    def is_compatible_with(cls, request):
        """
        Determine if the current request is compatible with this
        dispatcher.
        """

    def pre_dispatch(self, rule, args):
        """
        Prepare the system before dispatching the request to its
        controller. This method is often overridden in ir.http to
        extract some info from the request query-string or headers and
        to save them in the session or in the context.
        """
        routing = rule.endpoint.routing
        self.request.session.can_save = routing.get('save_session', True)

        set_header = self.request.future_response.headers.set
        cors = routing.get('cors')
        if cors:
            set_header('Access-Control-Allow-Origin', cors)
            set_header('Access-Control-Allow-Methods', (
                'POST' if routing['type'] == 'json'
                else ', '.join(routing['methods'] or ['GET', 'POST'])
            ))

        if cors and self.request.httprequest.method == 'OPTIONS':
            set_header('Access-Control-Max-Age', CORS_MAX_AGE)
            set_header('Access-Control-Allow-Headers',
                       'Origin, X-Requested-With, Content-Type, Accept, Authorization')
            werkzeug.exceptions.abort(Response(status=204))

        if 'max_content_length' in routing:
            max_content_length = routing['max_content_length']
            if callable(max_content_length):
                max_content_length = max_content_length(rule.endpoint.func.__self__)
            self.request.httprequest.max_content_length = max_content_length

    @abstractmethod
    def dispatch(self, endpoint, args):
        """
        Extract the params from the request's body and call the
        endpoint. While it is preferred to override ir.http._pre_dispatch
        and ir.http._post_dispatch, this method can be override to have
        a tight control over the dispatching.
        """

    def post_dispatch(self, response):
        """
        Manipulate the HTTP response to inject various headers, also
        save the session when it is dirty.
        """
        self.request._save_session()
        self.request._inject_future_response(response)
        root.set_csp(response)

    @abstractmethod
    def handle_error(self, exc: Exception) -> collections.abc.Callable:
        """
        Transform the exception into a valid HTTP response. Called upon
        any exception while serving a request.
        """

class HttpDispatcher(Dispatcher):
    routing_type = 'http'

    @classmethod
    def is_compatible_with(cls, request):
        return True

    def dispatch(self, endpoint, args):
        """
        Perform http-related actions such as deserializing the request
        body and query-string and checking cors/csrf while dispatching a
        request to a ``type='http'`` route.

        See :meth:`~inphms.http.Response.load` method for the compatible
        endpoint return types.
        """
        self.request.params = dict(self.request.get_http_params(), **args)

        # Check for CSRF token for relevant requests
        if self.request.httprequest.method not in CSRF_FREE_METHODS and endpoint.routing.get('csrf', True):
            if not self.request.db:
                return self.request.redirect('/web/database/selector')

            token = self.request.params.pop('csrf_token', None)
            if not self.request.validate_csrf(token):
                if token is not None:
                    _logger.warning("CSRF validation failed on path '%s'", self.request.httprequest.path)
                else:
                    _logger.warning(MISSING_CSRF_WARNING, request.httprequest.path)
                raise werkzeug.exceptions.BadRequest('Session expired (invalid CSRF token)')

        if self.request.db:
            return self.request.registry['ir.http']._dispatch(endpoint)
        else:
            return endpoint(**self.request.params)

    def handle_error(self, exc: Exception) -> collections.abc.Callable:
        """
        Handle any exception that occurred while dispatching a request
        to a `type='http'` route. Also handle exceptions that occurred
        when no route matched the request path, when no fallback page
        could be delivered and that the request ``Content-Type`` was not
        json.

        :param Exception exc: the exception that occurred.
        :returns: a WSGI application
        """
        if isinstance(exc, SessionExpiredException):
            session = self.request.session
            was_connected = session.uid is not None
            session.logout(keep_db=True)
            response = self.request.redirect_query('/web/login', {'redirect': self.request.httprequest.full_path})
            if was_connected:
                root.session_store.rotate(session, self.request.env)
                response.set_cookie('session_id', session.sid, max_age=get_session_max_inactivity(self.env), httponly=True)
            return response

        return (exc if isinstance(exc, HTTPException)
           else Forbidden(exc.args[0]) if isinstance(exc, (AccessDenied, AccessError))
           else BadRequest(exc.args[0]) if isinstance(exc, UserError)
           else InternalServerError()  # hide the real error
        )


# =========================================================
# Controller and routes
# =========================================================

class Controller(object):
    """
    Class mixin that provide module controllers the ability to serve
    content over http and to be extended in child modules.

    Each class :ref:`inheriting <python:tut-inheritance>` from
    :class:`~inphms.http.Controller` can use the :func:`~inphms.http.route`:
    decorator to route matching incoming web requests to decorated
    methods.

    Like models, controllers can be extended by other modules. The
    extension mechanism is different because controllers can work in a
    database-free environment and therefore cannot use
    :class:~inphms.api.Registry:.

    To *override* a controller, :ref:`inherit <python:tut-inheritance>`
    from its class, override relevant methods and re-expose them with
    :func:`~inphms.http.route`:. Please note that the decorators of all
    methods are combined, if the overriding method’s decorator has no
    argument all previous ones will be kept, any provided argument will
    override previously defined ones.

    .. code-block:

        class GreetingController(inphms.http.Controller):
            @route('/greet', type='http', auth='public')
            def greeting(self):
                return 'Hello'

        class UserGreetingController(GreetingController):
            @route(auth='user')  # override auth, keep path and type
            def greeting(self):
                return super().handler()
    """
    children_classes = collections.defaultdict(list)  # indexed by module

    @classmethod
    def __init_subclass__(cls):
        super().__init_subclass__()
        if Controller in cls.__bases__:
            path = cls.__module__.split('.')
            module = path[2] if path[:2] == ['inphms', 'addons'] else ''
            Controller.children_classes[module].append(cls)

def route(route=None, **routing):
    """
    Decorate a controller method in order to route incoming requests
    matching the given URL and options to the decorated method.

    .. warning::
        It is mandatory to re-decorate any method that is overridden in
        controller extensions but the arguments can be omitted. See
        :class:`~inphms.http.Controller` for more details.

    :param Union[str, Iterable[str]] route: The paths that the decorated
        method is serving. Incoming HTTP request paths matching this
        route will be routed to this decorated method. See `werkzeug
        routing documentation <http://werkzeug.pocoo.org/docs/routing/>`_
        for the format of route expressions.
    :param str type: The type of request, either ``'json'`` or
        ``'http'``. It describes where to find the request parameters
        and how to serialize the response.
    :param str auth: The authentication method, one of the following:

        * ``'user'``: The user must be authenticated and the current
          request will be executed using the rights of the user.
        * ``'bearer'``: The user is authenticated using an "Authorization"
          request header, using the Bearer scheme with an API token.
          The request will be executed with the permissions of the
          corresponding user. If the header is missing, the request
          must belong to an authentication session, as for the "user"
          authentication method.
        * ``'public'``: The user may or may not be authenticated. If he
          isn't, the current request will be executed using the shared
          Public user.
        * ``'none'``: The method is always active, even if there is no
          database. Mainly used by the framework and authentication
          modules. The request code will not have any facilities to
          access the current user.
    :param Iterable[str] methods: A list of http methods (verbs) this
        route applies to. If not specified, all methods are allowed.
    :param str cors: The Access-Control-Allow-Origin cors directive value.
    :param bool csrf: Whether CSRF protection should be enabled for the
        route. Enabled by default for ``'http'``-type requests, disabled
        by default for ``'json'``-type requests.
    :param Union[bool, Callable[[registry, request], bool]] readonly:
        Whether this endpoint should open a cursor on a read-only
        replica instead of (by default) the primary read/write database.
    :param Callable[[Exception], Response] handle_params_access_error:
        Implement a custom behavior if an error occurred when retrieving the record
        from the URL parameters (access error or missing error).
    """
    def decorator(endpoint):
        fname = f"<function {endpoint.__module__}.{endpoint.__name__}>"

        # Sanitize the routing
        assert routing.get('type', 'http') in _dispatchers.keys()
        if route:
            routing['routes'] = [route] if isinstance(route, str) else route
        wrong = routing.pop('method', None)
        if wrong is not None:
            _logger.warning("%s defined with invalid routing parameter 'method', assuming 'methods'", fname)
            routing['methods'] = wrong

        @functools.wraps(endpoint) # replaces the original function to route_wrapper()
        def route_wrapper(self, *args, **params):
            params_ok = filter_kwargs(endpoint, params)
            params_ko = set(params) - set(params_ok)
            if params_ko:
                _logger.warning("%s called ignoring args %s", fname, params_ko)

            result = endpoint(self, *args, **params_ok)
            if routing['type'] == 'http':  # _generate_routing_rules() ensures type is set
                return Response.load(result)
            return result

        route_wrapper.original_routing = routing
        route_wrapper.original_endpoint = endpoint
        return route_wrapper
    return decorator

# =========================================================
# WSGI Entry Point
# =========================================================
class Application:
    """ INPHMS WSGI Application """
    # See also: https://www.python.org/dev/peps/pep-3333
    def __call__(self, environ, start_response):
        """
        WSGI application entry point.

        :param dict environ: container for CGI environment variables
            such as the request HTTP headers, the source IP address and
            the body as an io file.
        :param callable start_response: function provided by the WSGI
            server that this application must call in order to send the
            HTTP response status line and the response headers.
        """
        current_thread = threading.current_thread()
        current_thread.query_count = 0
        current_thread.query_time = 0
        current_thread.perf_t0 = time.time()
        current_thread.cursor_mode = None
        if hasattr(current_thread, 'dbname'):
            del current_thread.dbname
        if hasattr(current_thread, 'uid'):
            del current_thread.uid

        if inphms.tools.config['proxy_mode'] and environ.get("HTTP_X_FORWARDED_HOST"):
            # The ProxyFix middleware has a side effect of updating the
            # environ, see https://github.com/pallets/werkzeug/pull/2184
            def fake_app(environ, start_response):
                return []
            def fake_start_response(status, headers):
                return
            ProxyFix(fake_app)(environ, fake_start_response)

        with HTTPRequest(environ) as httprequest:
            request = Request(httprequest)
            _request_stack.push(request)

            try:
                request._post_init()
                current_thread.url = httprequest.url

                if self.get_static_file(httprequest.path):
                    response = request._serve_static()
                elif request.db:
                    try:
                        with request._get_profiler_context_manager():
                            response = request._serve_db()
                    except RegistryError as e:
                        _logger.warning("Database or registry unusable, trying without", exc_info=e.__cause__)
                        request.db = None
                        request.session.logout()
                        if (httprequest.path.startswith('/inphms/')
                            or httprequest.path in (
                                '/inphms', '/web', '/web/login', '/test_http/ensure_db',
                            )):
                            # ensure_db() protected routes, remove ?db= from the query string
                            args_nodb = request.httprequest.args.copy()
                            args_nodb.pop('db', None)
                            request.reroute(httprequest.path, url_encode(args_nodb))
                        response = request._serve_nodb()
                else:
                    response = request._serve_nodb()
                return response(environ, start_response)

            except Exception as exc:
                # Valid (2xx/3xx) response returned via werkzeug.exceptions.abort.
                if isinstance(exc, HTTPException) and exc.code is None:
                    response = exc.get_response()
                    HttpDispatcher(request).post_dispatch(response)
                    return response(environ, start_response)

                # Logs the error here so the traceback starts with ``__call__``.
                if hasattr(exc, 'loglevel'):
                    _logger.log(exc.loglevel, exc, exc_info=getattr(exc, 'exc_info', None))
                elif isinstance(exc, HTTPException):
                    pass
                elif isinstance(exc, SessionExpiredException):
                    _logger.info(exc)
                elif isinstance(exc, (UserError, AccessError)):
                    _logger.warning(exc)
                else:
                    _logger.error("Exception during request handling.", exc_info=True)

                # Ensure there is always a WSGI handler attached to the exception.
                if not hasattr(exc, 'error_response'):
                    exc.error_response = request.dispatcher.handle_error(exc)

                return exc.error_response(environ, start_response)

            finally:
                _request_stack.pop()

    @lazy_property
    def session_store(self): #ichecked
        path = inphms.tools.config.session_dir
        _logger.debug('HTTP sessions stored in: %s', path)
        return FilesystemSessionStore(path, session_class=Session, renew_missing=True)
    

root = Application()