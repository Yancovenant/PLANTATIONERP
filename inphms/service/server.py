# Part of Inphms, see License file for full copyright and licensing details.

#-----------------------------------------------------------
# Threaded, Gevent and Prefork Servers
#-----------------------------------------------------------
import datetime
import errno
import logging
import os
import os.path
import platform
import random
import select
import signal
import socket
import subprocess
import sys
import threading
import time
import contextlib
from email.utils import parsedate_to_datetime
from io import BytesIO

import psutil
import werkzeug.serving

if os.name == 'posix':
    # Unix only for workers
    import fcntl
    import resource
    try:
        import inotify
        from inotify.adapters import InotifyTrees
        from inotify.constants import IN_MODIFY, IN_CREATE, IN_MOVED_TO
        INOTIFY_LISTEN_EVENTS = IN_MODIFY | IN_CREATE | IN_MOVED_TO
    except ImportError:
        inotify = None
else:
    # Windows shim
    signal.SIGHUP = -1
    inotify = None

if not inotify:
    try:
        import watchdog
        from watchdog.observers import Observer
        from watchdog.events import FileCreatedEvent, FileModifiedEvent, FileMovedEvent
    except ImportError:
        watchdog = None

# Optional process names for workers
try:
    from setproctitle import setproctitle
except ImportError:
    setproctitle = lambda x: None

import inphms
from inphms.tools import config
from inphms.modules.registry import Registry
from inphms.tools.misc import dumpstacks

_logger = logging.getLogger(__name__)

SLEEP_INTERVAL = 60

def set_limit_memory_hard(): #ichecked
    if platform.system() != 'Linux':
        return
    limit_memory_hard = config['limit_memory_hard']
    if inphms.evented and config['limit_memory_hard_gevent']:
        limit_memory_hard = config['limit_memory_hard_gevent']
    if limit_memory_hard:
        rlimit = resource.RLIMIT_AS
        soft, hard = resource.getrlimit(rlimit)
        resource.setrlimit(rlimit, (limit_memory_hard, hard))


def start(preload=None, stop=False):
    """ Start the inphms http server and cron processor.
    """
    global server

    load_server_wide_modules()
    if inphms.evented:
        server = GeventServer(inphms.http.root)
    elif config['workers']:
        if config['test_enable'] or config['test_file']:
            _logger.warning("Unit testing in workers mode could fail; use --workers 0.")

        server = PreforkServer(inphms.http.root)
    else:
        if platform.system() == "Linux" and sys.maxsize > 2**32 and "MALLOC_ARENA_MAX" not in os.environ:
            # glibc's malloc() uses arenas [1] in order to efficiently handle memory allocation of multi-threaded
            # applications. This allows better memory allocation handling in case of multiple threads that
            # would be using malloc() concurrently [2].
            # Due to the python's GIL, this optimization have no effect on multithreaded python programs.
            # Unfortunately, a downside of creating one arena per cpu core is the increase of virtual memory
            # which Inphms is based upon in order to limit the memory usage for threaded workers.
            # On 32bit systems the default size of an arena is 512K while on 64bit systems it's 64M [3],
            # hence a threaded worker will quickly reach it's default memory soft limit upon concurrent requests.
            # We therefore set the maximum arenas allowed to 2 unless the MALLOC_ARENA_MAX env variable is set.
            # Note: Setting MALLOC_ARENA_MAX=0 allow to explicitly set the default glibs's malloc() behaviour.
            #
            # [1] https://sourceware.org/glibc/wiki/MallocInternals#Arenas_and_Heaps
            # [2] https://www.gnu.org/software/libc/manual/html_node/The-GNU-Allocator.html
            # [3] https://sourceware.org/git/?p=glibc.git;a=blob;f=malloc/malloc.c;h=00ce48c;hb=0a8262a#l862
            try:
                import ctypes
                libc = ctypes.CDLL("libc.so.6")
                M_ARENA_MAX = -8
                assert libc.mallopt(ctypes.c_int(M_ARENA_MAX), ctypes.c_int(2))
            except Exception:
                _logger.warning("Could not set ARENA_MAX through mallopt()")
        server = ThreadedServer(inphms.http.root)

    watcher = None
    if 'reload' in config['dev_mode'] and not inphms.evented:
        if inotify:
            watcher = FSWatcherInotify()
            watcher.start()
        elif watchdog:
            watcher = FSWatcherWatchdog()
            watcher.start()
        else:
            if os.name == 'posix' and platform.system() != 'Darwin':
                module = 'inotify'
            else:
                module = 'watchdog'
            _logger.warning("'%s' module not installed. Code autoreload feature is disabled", module)

    rc = server.run(preload, stop)

    if watcher:
        watcher.stop()
    # like the legend of the phoenix, all ends with beginnings
    if server_phoenix:
        _reexec()

    return rc if rc else 0

#----------------------------------------------------------
# start/stop public api
#----------------------------------------------------------

server = None
server_phoenix = False

def load_server_wide_modules(): #ichecked
    server_wide_modules = list(inphms.conf.server_wide_modules)
    server_wide_modules.extend(m for m in ('base', 'web') if m not in server_wide_modules)
    for m in server_wide_modules:
        try:
            inphms.modules.module.load_inphms_module(m)
        except Exception:
            msg = ''
            if m == 'web':
                msg = """
The `web` module is provided by the addons found in the `inphms-web` project.
Maybe you forgot to add those addons in your addons_path configuration."""
            _logger.exception('Failed to load server-wide module `%s`.%s', m, msg)

def preload_registries(dbnames):
    """ Preload a registries, possibly run a test file."""
    # TODO: move all config checks to args dont check tools.config here
    dbnames = dbnames or []
    rc = 0
    for dbname in dbnames:
        try:
            update_module = config['init'] or config['update']
            threading.current_thread().dbname = dbname
            registry = Registry.new(dbname, update_module=update_module)

            # run test_file if provided
            if config['test_file']:
                test_file = config['test_file']
                if not os.path.isfile(test_file):
                    _logger.warning('test file %s cannot be found', test_file)
                elif not test_file.endswith('py'):
                    _logger.warning('test file %s is not a python file', test_file)
                else:
                    _logger.info('loading test file %s', test_file)
                    load_test_file_py(registry, test_file)

            # run post-install tests
            if config['test_enable']:
                from inphms.tests import loader  # noqa: PLC0415
                t0 = time.time()
                t0_sql = inphms.sql_db.sql_counter
                module_names = (registry.updated_modules if update_module else
                                sorted(registry._init_modules))
                _logger.info("Starting post tests")
                tests_before = registry._assertion_report.testsRun
                post_install_suite = loader.make_suite(module_names, 'post_install')
                if post_install_suite.has_http_case():
                    with registry.cursor() as cr:
                        env = inphms.api.Environment(cr, inphms.SUPERUSER_ID, {})
                        env['ir.qweb']._pregenerate_assets_bundles()
                result = loader.run_suite(post_install_suite, global_report=registry._assertion_report)
                registry._assertion_report.update(result)
                _logger.info("%d post-tests in %.2fs, %s queries",
                             registry._assertion_report.testsRun - tests_before,
                             time.time() - t0,
                             inphms.sql_db.sql_counter - t0_sql)

                registry._assertion_report.log_stats()
            if registry._assertion_report and not registry._assertion_report.wasSuccessful():
                rc += 1
        except Exception:
            _logger.critical('Failed to initialize database `%s`.', dbname, exc_info=True)
            return -1
    return rc

def memory_info(process): #ichecked
    """
    :return: the relevant memory usage according to the OS in bytes.
    """
    # psutil < 2.0 does not have memory_info, >= 3.0 does not have get_memory_info
    pmem = (getattr(process, 'memory_info', None) or process.get_memory_info)()
    # MacOSX allocates very large vms to all processes so we only monitor the rss usage.
    if platform.system() == 'Darwin':
        return pmem.rss
    return pmem.vms

class CommonServer(object): #ichecked
    _on_stop_funcs = []
    def __init__(self, app):
        self.app = app ## inphms.http.root
        # config
        self.interface = config['http_interface'] or '0.0.0.0'
        self.port = config['http_port']
        # runtime
        self.pid = os.getpid()
    
    @classmethod
    def on_stop(cls, func): #ichecked
        """ Register a cleanup function to be executed when the server stops """
        cls._on_stop_funcs.append(func)
        
    def stop(self): #ichecked
        for func in self._on_stop_funcs:
            try:
                _logger.debug("on_close call %s", func)
                func()
            except Exception:
                _logger.warning("Exception in %s", func.__name__, exc_info=True)
    
    def close_socket(self, sock):
        """ Closes a socket instance cleanly
        :param sock: the network socket to close
        :type sock: socket.socket
        """
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except socket.error as e:
            if e.errno == errno.EBADF:
                # Werkzeug > 0.9.6 closes the socket itself (see commit
                # https://github.com/mitsuhiko/werkzeug/commit/4d8ca089)
                return
            # On OSX, socket shutdowns both sides if any side closes it
            # causing an error 57 'Socket is not connected' on shutdown
            # of the other side (or something), see
            # http://bugs.python.org/issue4397
            # note: stdlib fixed test, not behavior
            if e.errno != errno.ENOTCONN or platform.system() not in ['Darwin', 'Windows']:
                raise
        sock.close()

class ThreadedServer(CommonServer):
    def __init__(self, app): #ichecked
        super(ThreadedServer, self).__init__(app)
        self.main_thread_id = threading.current_thread().ident
        # Variable keeping track of the number of calls to the signal handler defined
        # below. This variable is monitored by ``quit_on_signals()``.
        self.quit_signals_received = 0

        #self.socket = None
        self.httpd = None
        self.limits_reached_threads = set()
        self.limit_reached_time = None

    def run(self, preload=None, stop=False): #ichecked
        """ Start the http server and the cron thread then wait for a signal.

        The first SIGINT or SIGTERM signal will initiate a graceful shutdown while
        a second one if any will force an immediate exit.
        """
        with Registry._lock:
            self.start(stop=stop)
            rc = preload_registries(preload)
        
        if stop:
            if config['test_enable']:
                from inphms.tests.result import _logger as logger  # noqa: PLC0415
                with Registry.registries._lock:
                    for db, registry in Registry.registries.d.items():
                        report = registry._assertion_report
                        log = logger.error if not report.wasSuccessful() \
                         else logger.warning if not report.testsRun \
                         else logger.info
                        log("%s when loading database %r", report, db)
            self.stop()
            return rc

        self.cron_spawn()

        # Wait for a first signal to be handled. (time.sleep will be interrupted
        # by the signal handler)
        try:
            while self.quit_signals_received == 0:
                self.process_limit()
                if self.limit_reached_time:
                    has_other_valid_requests = any(
                        not t.daemon and
                        t not in self.limits_reached_threads
                        for t in threading.enumerate()
                        if getattr(t, 'type', None) == 'http')
                    if (not has_other_valid_requests or
                            (time.time() - self.limit_reached_time) > SLEEP_INTERVAL):
                        # We wait there is no processing requests
                        # other than the ones exceeding the limits, up to 1 min,
                        # before asking for a reload.
                        _logger.info('Dumping stacktrace of limit exceeding threads before reloading')
                        dumpstacks(thread_idents=[thread.ident for thread in self.limits_reached_threads])
                        self.reload()
                        # `reload` increments `self.quit_signals_received`
                        # and the loop will end after this iteration,
                        # therefore leading to the server stop.
                        # `reload` also sets the `server_phoenix` flag
                        # to tell the server to restart the server after shutting down.
                    else:
                        time.sleep(1)
                else:
                    time.sleep(SLEEP_INTERVAL)
        except KeyboardInterrupt:
            pass

        self.stop()

    def start(self, stop=False): #ichecked
        _logger.debug("Setting signal handlers")
        set_limit_memory_hard()
        if os.name == 'posix':
            signal.signal(signal.SIGINT, self.signal_handler) # Signal Interupt CTRL + C
            signal.signal(signal.SIGTERM, self.signal_handler) # Signal Terminate, `kill` command
            signal.signal(signal.SIGCHLD, self.signal_handler) # Signal Child, when a child process dies
            signal.signal(signal.SIGHUP, self.signal_handler) # Signal Hang Up, connection dropped
            signal.signal(signal.SIGXCPU, self.signal_handler) # Signal CPU Time Limit Exceeded
            signal.signal(signal.SIGQUIT, dumpstacks) # Signal Quit, `kill -QUIT` command or CTRL + \
            signal.signal(signal.SIGUSR1, log_ormcache_stats) # Signal User 1, `kill -USR1 <pid>` command
        elif os.name == 'nt':
            import win32api
            win32api.SetConsoleCtrlHandler(lambda sig: self.signal_handler(sig, None), 1)
        
        test_mode = config['test_enable'] or config['test_file']
        if test_mode or (config['http_enable'] and not stop):
            # some tests need the http daemon to be available...
            self.http_spawn()
    
    def stop(self): #ichecked
        """ Shutdown the WSGI server. Wait for non daemon threads.
        """
        if server_phoenix:
            _logger.info("Initiating server reload")
        else:
            _logger.info("Initiating shutdown")
            _logger.info("Hit CTRL-C again or send a second signal to force the shutdown.")

        stop_time = time.time()

        if self.httpd:
            self.httpd.shutdown()

        super().stop()

        # Manually join() all threads before calling sys.exit() to allow a second signal
        # to trigger _force_quit() in case some non-daemon threads won't exit cleanly.
        # threading.Thread.join() should not mask signals (at least in python 2.5).
        me = threading.current_thread()
        _logger.debug('current thread: %r', me)
        for thread in threading.enumerate():
            _logger.debug('process %r (%r)', thread, thread.daemon)
            if (thread != me and not thread.daemon and thread.ident != self.main_thread_id and
                    thread not in self.limits_reached_threads):
                while thread.is_alive() and (time.time() - stop_time) < 1:
                    # We wait for requests to finish, up to 1 second.
                    _logger.debug('join and sleep')
                    # Need a busyloop here as thread.join() masks signals
                    # and would prevent the forced shutdown.
                    thread.join(0.05)
                    time.sleep(0.05)

        inphms.sql_db.close_all()

        _logger.debug('--')
        logging.shutdown()
    
    def reload(self): #ichecked
        os.kill(self.pid, signal.SIGHUP)
    
    def http_spawn(self): #ichecked
        self.httpd = ThreadedWSGIServerReloadable(self.interface, self.port, self.app)
        threading.Thread(
            target=self.httpd.serve_forever,
            name="inphms.service.httpd",
            daemon=True,
        ).start()
    
    def cron_spawn(self): #ichecked
        """ Start the above runner function in a daemon thread.

        The thread is a typical daemon thread: it will never quit and must be
        terminated when the main process exits - with no consequence (the processing
        threads it spawns are not marked daemon).

        """
        # Force call to strptime just before starting the cron thread
        # to prevent time.strptime AttributeError within the thread.
        # See: http://bugs.python.org/issue7980
        datetime.datetime.strptime('2012-01-01', '%Y-%m-%d')
        for i in range(inphms.tools.config['max_cron_threads']):
            def target():
                self.cron_thread(i)
            t = threading.Thread(target=target, name="inphms.service.cron.cron%d" % i)
            t.daemon = True
            t.type = 'cron'
            t.start()
            _logger.debug("cron%d started!" % i)
    
    def cron_thread(self, number): #ichecked
        # Steve Reich timing style with thundering herd mitigation.
        #
        # On startup, all workers bind on a notification channel in
        # postgres so they can be woken up at will. At worst they wake
        # up every SLEEP_INTERVAL with a jitter. The jitter creates a
        # chorus effect that helps distribute on the timeline the moment
        # when individual worker wake up.
        #
        # On NOTIFY, all workers are awaken at the same time, sleeping
        # just a bit prevents they all poll the database at the exact
        # same time. This is known as the thundering herd effect.

        from inphms.addons.base.models.ir_cron import ir_cron
        def _run_cron(cr): #ichecked
            pg_conn = cr._cnx
            # LISTEN / NOTIFY doesn't work in recovery mode
            cr.execute("SELECT pg_is_in_recovery()")
            in_recovery = cr.fetchone()[0]
            if not in_recovery:
                cr.execute("LISTEN cron_trigger")
            else:
                _logger.warning("PG cluster in recovery mode, cron trigger not activated")
            cr.commit()
            alive_time = time.monotonic()
            while config['limit_time_worker_cron'] <= 0 or (time.monotonic() - alive_time) <= config['limit_time_worker_cron']:
                select.select([pg_conn], [], [], SLEEP_INTERVAL + number)
                time.sleep(number / 100)
                pg_conn.poll()

                registries = inphms.modules.registry.Registry.registries
                _logger.debug('cron%d polling for jobs', number)
                for db_name, registry in registries.d.items():
                    if registry.ready:
                        thread = threading.current_thread()
                        thread.start_time = time.time()
                        try:
                            ir_cron._process_jobs(db_name)
                        except Exception:
                            _logger.warning('cron%d encountered an Exception:', number, exc_info=True)
                        thread.start_time = None
        while True:
            conn = inphms.sql_db.db_connect('postgres')
            with contextlib.closing(conn.cursor()) as cr:
                _run_cron(cr)
                cr._cnx.close()
            _logger.info('cron%d max age (%ss) reached, releasing connection.', number, config['limit_time_worker_cron'])
        
    def process_limit(self): #ichecked
        memory = memory_info(psutil.Process(os.getpid()))
        if config['limit_memory_soft'] and memory > config['limit_memory_soft']:
            _logger.warning('Server memory limit (%s) reached.', memory)
            self.limits_reached_threads.add(threading.current_thread())

        for thread in threading.enumerate():
            thread_type = getattr(thread, 'type', None)
            if not thread.daemon and thread_type != 'websocket' or thread_type == 'cron':
                # We apply the limits on cron threads and HTTP requests,
                # websocket requests excluded.
                if getattr(thread, 'start_time', None):
                    thread_execution_time = time.time() - thread.start_time
                    thread_limit_time_real = config['limit_time_real']
                    if (getattr(thread, 'type', None) == 'cron' and
                            config['limit_time_real_cron'] and config['limit_time_real_cron'] > 0):
                        thread_limit_time_real = config['limit_time_real_cron']
                    if thread_limit_time_real and thread_execution_time > thread_limit_time_real:
                        _logger.warning(
                            'Thread %s virtual real time limit (%d/%ds) reached.',
                            thread, thread_execution_time, thread_limit_time_real)
                        self.limits_reached_threads.add(thread)
        # Clean-up threads that are no longer alive
        # e.g. threads that exceeded their real time,
        # but which finished before the server could restart.
        for thread in list(self.limits_reached_threads):
            if not thread.is_alive():
                self.limits_reached_threads.remove(thread)
        if self.limits_reached_threads:
            self.limit_reached_time = self.limit_reached_time or time.time()
        else:
            self.limit_reached_time = None
    
    def signal_handler(self, sig, frame): #ichecked
        if sig in [signal.SIGINT, signal.SIGTERM]:
            # shutdown on kill -INT or -TERM
            self.quit_signals_received += 1
            if self.quit_signals_received > 1:
                # logging.shutdown was already called at this point.
                sys.stderr.write("Forced shutdown.\n")
                os._exit(0)
            # interrupt run() to start shutdown
            raise KeyboardInterrupt()
        elif hasattr(signal, 'SIGXCPU') and sig == signal.SIGXCPU:
            sys.stderr.write("CPU time limit exceeded! Shutting down immediately\n")
            sys.stderr.flush()
            os._exit(0)
        elif sig == signal.SIGHUP:
            # restart on kill -HUP
            global server_phoenix  # noqa: PLW0603
            server_phoenix = True # Set restart flag
            self.quit_signals_received += 1
            # interrupt run() to start shutdown
            raise KeyboardInterrupt()


#----------------------------------------------------------
# Werkzeug WSGI servers patched
#----------------------------------------------------------
class LoggingBaseWSGIServerMixIn(object): #ichecked
    def handle_error(self, request, client_address):
        t, e, _ = sys.exc_info()
        if t == socket.error and e.errno == errno.EPIPE:
            # broken pipe, ignore error,
            # happens when client disconnects while server is sending data
            return
        _logger.exception('Exception happened during processing of request from %s', client_address)

class ThreadedWSGIServerReloadable(LoggingBaseWSGIServerMixIn, werkzeug.serving.ThreadedWSGIServer):
    """ werkzeug Threaded WSGI Server patched to allow reusing a listen socket
    given by the environment, this is used by autoreload to keep the listen
    socket open when a reload happens.
    """
    def __init__(self, host, port, app): #ichecked
        # The INPHMS_MAX_HTTP_THREADS environment variable allows to limit the amount of concurrent
        # socket connections accepted by a threaded server, implicitly limiting the amount of
        # concurrent threads running for http requests handling.
        self.max_http_threads = os.environ.get("INPHMS_MAX_HTTP_THREADS")
        if self.max_http_threads:
            try:
                self.max_http_threads = int(self.max_http_threads)
            except ValueError:
                # If the value can't be parsed to an integer then it's computed in an automated way to
                # half the size of db_maxconn because while most requests won't borrow cursors concurrently
                # there are some exceptions where some controllers might allocate two or more cursors.
                self.max_http_threads = max((config['db_maxconn'] - config['max_cron_threads']) // 2, 1)
            # Semaphore is a thread-safe counter, used to limit the number of concurrent threads.
            # Like a "bouncer" at a club, only allow a certain number of people in at a time.
            self.http_threads_sem = threading.Semaphore(self.max_http_threads)
        super().__init__(host, port, app, handler=RequestHandler)

        # See https://github.com/pallets/werkzeug/pull/770
        # This allow the request threads to not be set as daemon
        # so the server waits for them when shutting down gracefully.
        self.daemon_threads = False # werkzeug attribute
    
    def server_bind(self): #ichecked
        SD_LISTEN_FDS_START = 3 # systemd on linux, listen on socket fd
        if os.environ.get('LISTEN_FDS') == '1' and os.environ.get('LISTEN_PID') == str(os.getpid()):
            self.reload_socket = True
            self.socket = socket.fromfd(SD_LISTEN_FDS_START, socket.AF_INET, socket.SOCK_STREAM)
            _logger.info('HTTP service (werkzeug) running through socket activation')
        else: #normal case, bind to port
            # TODO: check windows equivalent to systemd door building
            #       SCM, Service Control Manager <- need to check
            #       IIS, Internet Information Services <- need to check
            self.reload_socket = False
            super().server_bind()
            _logger.info('HTTP service (werkzeug) running on %s:%s', self.server_name, self.server_port)
        
    def server_activate(self): #ichecked
        if not self.reload_socket:
            super().server_activate()

    def _handle_request_noblock(self): #ichecked
        if self.max_http_threads and not self.http_threads_sem.acquire(timeout=0.1):
            # If the semaphore is full we will return immediately to the upstream (most probably
            # socketserver.BaseServer's serve_forever loop  which will retry immediately as the
            # selector will find a pending connection to accept on the socket. There is a 100 ms
            # penalty in such case in order to avoid cpu bound loop while waiting for the semaphore.
            return
        # upstream _handle_request_noblock will handle errors and call shutdown_request in any cases
        super()._handle_request_noblock()
    
    def process_request(self, request, client_address): #ichecked
        """
        Start a new thread to process the request.
        Override the default method of class socketserver.ThreadingMixIn
        to be able to get the thread object which is instantiated
        and set its start time as an attribute
        """
        t = threading.Thread(target = self.process_request_thread,
                             args = (request, client_address))
        t.daemon = self.daemon_threads
        t.type = 'http'
        t.start_time = time.time()
        t.start()

    def shutdown_request(self, request): #ichecked
        if self.max_http_threads:
            # upstream is supposed to call this function no matter what happens during processing
            self.http_threads_sem.release()
        super().shutdown_request(request)

class RequestHandler(werkzeug.serving.WSGIRequestHandler):
    def __init__(self, *args, **kwargs): #ichecked
        self._sent_date_header = None
        self._sent_server_header = None
        super().__init__(*args, **kwargs)
    
    def setup(self): #ichecked
        # timeout to avoid chrome headless preconnect during tests
        if config['test_enable'] or config['test_file']:
            self.timeout = 5
        # flag the current thread as handling a http request
        super().setup()
        me = threading.current_thread()
        me.name = 'inphms.service.http.request.%s' % (me.ident,)
    
    def log_error(self, format, *args): #ichecked
        if format == "Request timed out: %r" and config['test_enable']:
            _logger.info(format, *args)
        else:
            super().log_error(format, *args)
    
    def send_header(self, keyword, value): #ichecked
        # Prevent `WSGIRequestHandler` from sending the connection close header (compatibility with werkzeug >= 2.1.1 )
        # since it is incompatible with websocket.
        if self.headers.get('Upgrade') == 'websocket' and keyword == 'Connection' and value == 'close':
            # Do not keep processing requests.
            self.close_connection = True
            return

        if keyword.casefold() == 'date':
            if self._sent_date_header is None:
                self._sent_date_header = value
            elif self._sent_date_header == value:
                return  # don't send the same header twice
            else:
                sent_datetime = parsedate_to_datetime(self._sent_date_header)
                new_datetime = parsedate_to_datetime(value)
                if sent_datetime == new_datetime:
                    return  # don't send the same date twice (differ in format)
                if abs((sent_datetime - new_datetime).total_seconds()) <= 1:
                    return  # don't send the same date twice (jitter of 1 second)
                _logger.warning(
                    "sending two different Date response headers: %r vs %r",
                    self._sent_date_header, value)

        if keyword.casefold() == 'server':
            if self._sent_server_header is None:
                self._sent_server_header = value
            elif self._sent_server_header == value:
                return  # don't send the same header twice
            else:
                _logger.warning(
                    "sending two different Server response headers: %r vs %r",
                    self._sent_server_header, value)

        super().send_header(keyword, value)
    
    def end_headers(self, *a, **kw): #ichecked
        super().end_headers(*a, **kw)
        # At this point, Werkzeug assumes the connection is closed and will discard any incoming
        # data. In the case of WebSocket connections, data should not be discarded. Replace the
        # rfile/wfile of this handler to prevent any further action (compatibility with werkzeug >= 2.3.x).
        # See: https://github.com/pallets/werkzeug/blob/2.3.x/src/werkzeug/serving.py#L334
        if self.headers.get('Upgrade') == 'websocket':
            self.rfile = BytesIO()
            self.wfile = BytesIO()

    def make_environ(self): #ichecked
        environ = super().make_environ()
        # Add the TCP socket to environ in order for the websocket
        # connections to use it.
        environ['socket'] = self.connection
        if self.headers.get('Upgrade') == 'websocket':
            # Since the upgrade header is introduced in version 1.1, Firefox
            # won't accept a websocket connection if the version is set to
            # 1.0.
            self.protocol_version = "HTTP/1.1"
        return environ