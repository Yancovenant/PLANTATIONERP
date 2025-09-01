# -*- coding: utf-8 -*-
# Part of Inphms. See LICENSE file for full copyright and licensing details.

from datetime import datetime
import gc
import json
import logging
import sys
import time
import threading
import re
import functools

from psycopg2 import OperationalError

from inphms import tools
from inphms.tools import SQL


_logger = logging.getLogger(__name__)

# ensure we have a non patched time for profiling times when using freezegun
real_datetime_now = datetime.now
real_time = time.time.__call__

def make_session(name=''):
    return f'{real_datetime_now():%Y-%m-%d %H:%M:%S} {name}'

def get_current_frame(thread=None):
    if thread:
        frame = sys._current_frames()[thread.ident]
    else:
        frame = sys._getframe()
    while frame.f_code.co_filename == __file__:
        frame = frame.f_back
    return frame

def _format_frame(frame):
    code = frame.f_code
    return (code.co_filename, frame.f_lineno, code.co_name, '')

def _get_stack_trace(frame, limit_frame=None):
    stack = []
    while frame is not None and frame != limit_frame:
        stack.append(_format_frame(frame))
        frame = frame.f_back
    if frame is None and limit_frame:
        _logger.error("Limit frame was not found")
    return list(reversed(stack))


class Collector:
    """
    Base class for objects that collect profiling data.

    A collector object is used by a profiler to collect profiling data, most
    likely a list of stack traces with time and some context information added
    by ExecutionContext decorator on current thread.

    This is a generic implementation of a basic collector, to be inherited.
    It defines default behaviors for creating an entry in the collector.
    """
    name = None                 # symbolic name of the collector
    _registry = {}              # map collector names to their class

    def __init__(self):
        self._processed = False
        self._entries = []
        self.profiler = None
    
    @classmethod
    def __init_subclass__(cls):
        if cls.name:
            cls._registry[cls.name] = cls
            cls._registry[cls.__name__] = cls
    
    @classmethod
    def make(cls, name, *args, **kwargs):
        """ Instantiate a collector corresponding to the given name. """
        return cls._registry[name](*args, **kwargs)


class Profiler:
    """
    Context manager to use to start the recording of some execution.
    Will save sql and async stack trace by default.
    """
    def __init__(self, collectors=None, db=..., profile_session=None,
                 description=None, disable_gc=False, params=None, log=False):
        """
        :param db: database name to use to save results.
            Will try to define database automatically by default.
            Use value ``None`` to not save results in a database.
        :param collectors: list of string and Collector object Ex: ['sql', PeriodicCollector(interval=0.2)]. Use `None` for default collectors
        :param profile_session: session description to use to reproup multiple profile. use make_session(name) for default format.
        :param description: description of the current profiler Suggestion: (route name/test method/loading module, ...)
        :param disable_gc: flag to disable gc durring profiling (usefull to avoid gc while profiling, especially during sql execution)
        :param params: parameters usable by collectors (like frame interval)
        """
        self.start_time = 0
        self.duration = 0
        self.profile_session = profile_session or make_session()
        self.description = description
        self.init_frame = None
        self.init_stack_trace = None
        self.init_thread = None
        self.disable_gc = disable_gc
        self.filecache = {}
        self.params = params or {}  # custom parameters usable by collectors
        self.profile_id = None
        self.log = log
        self.sub_profilers = []
        self.entry_count_limit = int(self.params.get("entry_count_limit", 0))   # the limit could be set using a smarter way
        self.done = False

        if db is ...:
            # determine database from current thread
            db = getattr(threading.current_thread(), 'dbname', None)
            if not db:
                # only raise if path is not given and db is not explicitely disabled
                raise Exception('Database name cannot be defined automaticaly. \n Please provide a valid/falsy dbname or path parameter')
        self.db = db

        # collectors
        if collectors is None:
            collectors = ['sql', 'traces_async']
        self.collectors = []
        for collector in collectors:
            if isinstance(collector, str):
                try:
                    collector = Collector.make(collector)
                except Exception:
                    _logger.error("Could not create collector with name %r", collector)
                    continue
            collector.profiler = self
            self.collectors.append(collector)

    def __enter__(self):
        self.init_thread = threading.current_thread()
        try:
            self.init_frame = get_current_frame(self.init_thread)
            self.init_stack_trace = _get_stack_trace(self.init_frame)
        except KeyError:
            # when using thread pools (gevent) the thread won't exist in the current_frames
            # this case is managed by http.py but will still fail when adding a profiler
            # inside a piece of code that may be called by a longpolling route.
            # in this case, avoid crashing the caller and disable all collectors
            self.init_frame = self.init_stack_trace = self.collectors = []
            self.db = self.params = None
            message = "Cannot start profiler, thread not found. Is the thread part of a thread pool?"
            if not self.description:
                self.description = message
            _logger.warning(message)

        if self.description is None:
            frame = self.init_frame
            code = frame.f_code
            self.description = f"{frame.f_code.co_name} ({code.co_filename}:{frame.f_lineno})"
        if self.params:
            self.init_thread.profiler_params = self.params
        if self.disable_gc and gc.isenabled():
            gc.disable()
        self.start_time = real_time()
        for collector in self.collectors:
            collector.start()
        return self

    def __exit__(self, *args):
        self.end()
    

class QwebTracker():

    @classmethod
    def wrap_render(cls, method_render):
        @functools.wraps(method_render)
        def _tracked_method_render(self, template, values=None, **options):
            current_thread = threading.current_thread()
            execution_context_enabled = getattr(current_thread, 'profiler_params', {}).get('execution_context_qweb')
            qweb_hooks = getattr(current_thread, 'qweb_hooks', ())
            if execution_context_enabled or qweb_hooks:
                # To have the new compilation cached because the generated code will change.
                # Therefore 'profile' is a key to the cache.
                options['profile'] = True
            return method_render(self, template, values, **options)
        return _tracked_method_render
    
    @classmethod
    def wrap_compile(cls, method_compile):
        @functools.wraps(method_compile)
        def _tracked_compile(self, template):
            if not self.env.context.get('profile'):
                return method_compile(self, template)

            template_functions, def_name = method_compile(self, template)
            render_template = template_functions[def_name]

            def profiled_method_compile(self, values):
                options = template_functions['options']
                ref = options.get('ref')
                ref_xml = options.get('ref_xml')
                qweb_tracker = QwebTracker(ref, ref_xml, self.env.cr)
                self = self.with_context(qweb_tracker=qweb_tracker)
                if qweb_tracker.execution_context_enabled:
                    with ExecutionContext(template=ref):
                        return render_template(self, values)
                return render_template(self, values)
            template_functions[def_name] = profiled_method_compile

            return (template_functions, def_name)
        return _tracked_compile

    @classmethod
    def wrap_compile_directive(cls, method_compile_directive):
        @functools.wraps(method_compile_directive)
        def _tracked_compile_directive(self, el, options, directive, level):
            if not options.get('profile') or directive in ('inner-content', 'tag-open', 'tag-close'):
                return method_compile_directive(self, el, options, directive, level)
            enter = f"{' ' * 4 * level}self.env.context['qweb_tracker'].enter_directive({directive!r}, {el.attrib!r}, {options['_qweb_error_path_xml'][0]!r})"
            leave = f"{' ' * 4 * level}self.env.context['qweb_tracker'].leave_directive({directive!r}, {el.attrib!r}, {options['_qweb_error_path_xml'][0]!r})"
            code_directive = method_compile_directive(self, el, options, directive, level)
            return [enter, *code_directive, leave] if code_directive else []
        return _tracked_compile_directive
    
    def __init__(self, view_id, arch, cr):
        current_thread = threading.current_thread()  # don't store current_thread on self
        self.execution_context_enabled = getattr(current_thread, 'profiler_params', {}).get('execution_context_qweb')
        self.qweb_hooks = getattr(current_thread, 'qweb_hooks', ())
        self.context_stack = []
        self.cr = cr
        self.view_id = view_id
        for hook in self.qweb_hooks:
            hook('render', self.cr.sql_log_count, view_id=view_id, arch=arch)

    def enter_directive(self, directive, attrib, xpath):
        execution_context = None
        if self.execution_context_enabled:
            directive_info = {}
            if ('t-' + directive) in attrib:
                directive_info['t-' + directive] = repr(attrib['t-' + directive])
            if directive == 'set':
                if 't-value' in attrib:
                    directive_info['t-value'] = repr(attrib['t-value'])
                if 't-valuef' in attrib:
                    directive_info['t-valuef'] = repr(attrib['t-valuef'])

                for key in attrib:
                    if key.startswith('t-set-') or key.startswith('t-setf-'):
                        directive_info[key] = repr(attrib[key])
            elif directive == 'foreach':
                directive_info['t-as'] = repr(attrib['t-as'])
            elif directive == 'groups' and 'groups' in attrib and not directive_info.get('t-groups'):
                directive_info['t-groups'] = repr(attrib['groups'])
            elif directive == 'att':
                for key in attrib:
                    if key.startswith('t-att-') or key.startswith('t-attf-'):
                        directive_info[key] = repr(attrib[key])
            elif directive == 'options':
                for key in attrib:
                    if key.startswith('t-options-'):
                        directive_info[key] = repr(attrib[key])
            elif ('t-' + directive) not in attrib:
                directive_info['t-' + directive] = None

            execution_context = tools.profiler.ExecutionContext(**directive_info, xpath=xpath)
            execution_context.__enter__()
            self.context_stack.append(execution_context)

        for hook in self.qweb_hooks:
            hook('enter', self.cr.sql_log_count, view_id=self.view_id, xpath=xpath, directive=directive, attrib=attrib)

    def leave_directive(self, directive, attrib, xpath):
        if self.execution_context_enabled:
            self.context_stack.pop().__exit__()

        for hook in self.qweb_hooks:
            hook('leave', self.cr.sql_log_count, view_id=self.view_id, xpath=xpath, directive=directive, attrib=attrib)