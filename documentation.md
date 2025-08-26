
## Project structure call chaining.

# CLI & Executable Runtime (services)

- run by using `python inphms-bin` or `python inphms-bin --server`
- will call @cli.main() alias @cli.command.main()

@cli.main() do:
- parsing args. (config)
    - args = sys.argv[1:] # Get CLI arguments
    - Handle --addons-path early if present
    - Set default command to "server"
- @initialize system path (config) <- THIS IS ONLY RUN IF THERE IS A COMMAND EXPLICITLY DEFINED IN COMMAND LINES. otherwise skip
    - at inphms.modules.module
    - it would hook/connect both the base/addons path to inphms.addons.__path__
- Execute chosen command
    - commands["server"]() if no command specified
    - commands["command]() if command specified
    - command.run()

@cli.server().run() do:
- check root user (posix) if root user show warning.
- inphms.tools.config.parse_config() (config):
    - this would parse the args given and populate it.
    - the first configmanager() instantiate will only populating default value without arguments
    - and this would run _parse_config() with new args data if exists,
    - init_logger()
    - and then @initialize_sys_path() again. but this is safely because only add addons if not already there.
- check postgres user
    - if db_user == postgres (default superuser)
    - exit, as it is a security issues, on production application.
- report configuration
    - just report logging output the config options.
- csv set limit size to 500MB for larger data imports
- preload, still not using this
- stop after init, if defined will force shutdown after run.
- setup_pid_file() will create if not evented (not a process/background task)
- THEN last is running the server with, inphms.service.server.start() - will return an rc (return code),
- to be used when system.exit(return code)

@service.server.start() do:
- loadserverwidemodules, (web, base) will be imported.
    - this would register all the addons module.
- individually import and append to sys.modules and run post_load function, to do some setup if defined.
- being read by __manifest__.py
- setup the correct Server Class, ThreadedServer + APP => inphms.http.root mostly and .run() it.
    - the groundwork would be something like this:
        a. server (ThreadedServer) // server is serving
        b. app (Application, inphms.http.root) // application
        c. client (Webserver) // to customer a.k.a web browsers
- automatically create instance of inphms.http.root = Application().
- this would handle all the request http coming to the server.
    

@ThreadedServer do:
    - run() :
        - will use registry.lock to self.start() the server.
    - start() :
        - will set memory limit, ONLY FOR LINUX
            - using config value, gevent, or original hard limit
            - setting limit by:
                - RLIMIT_AS -> maximum virtual memory a process can use.
                - get current limit, soft, hard -> cannot be exceed
                - set new limit, soft.
        - Setting up signal to be used by signal_handler
        - if config['http_enabled'] will do @http_spawn()
            - @http_spawn() do :
                - self.httpd -> @ThreadedWSGIServerReloadable(network interfaces, port, app)
                - start thread daemon, targeting the self.httpd.serve_forever
    - signal_handler() :
        - on SIGINT or SIGTERM, will gracefull shutdown first, then force shutdown
        - on MEMORY LIMIT EXCEED, will force shutdown.
        - on HangUp, this is the cool part.
            - uses server_phoenix to flag `restart after shutdown`
            - `phoenix = mythical bird that rises from ashes`
            - shutdown gracefully then restart.
    - IF BEING RUN with --stop-after-init, it would stop after server.start()
    - cron_spawn() : // setting up a multiple background jobs, to handle database connection,
                        processes, pending task, scheduled task
        - take --max-cron-threads value when instancing cron background threads
        - run in background @cron_thread()
        @cron_thread() do : while True // forever
            - it would use @db_connect('postgres') <- db name
            - this conn => Connection() Class which have ConnectionPool, dsn, dbname.
            - Take the Cursor() and use contextlib as a way to auto cleanup.
            @Cursor() class do :
                - when init it would,
                - ConnectionPool.borrow(dsn) // creating new connection or removing the dead, or reuse the connection.
                - set _obj -> psycopg2 cursor object
                - set caller / traceback
                - set connection isolation level to repeatable read.
                - and session with readonly true/false
            @ConnectionPool() do :
                - borrow() :
                    - would remove any idle, dead, leaked
                    - would reuse the connection, using reset(), and early return
                    - would check if max_conn exceed, if so remove the first not used connection.
                    - else, would create new psycopg2 connection.
                    - return psycopg2 object
            - Will forever run _run_cron() method.
            - _run_cron() do :
                - if in recovery mode, setup cron triggers.
                - (recovery mode returns true if pg database is standby/replica, or read only syncing from primary)
                - (returns false, if pg database is primary/master)
                - cron trigger is postgresql features that other processes can send `NOTIFY cron_triggers`
                @Registry.registries :
                    - will return LRU, `least recently used` that act like a cache. by josiah carlson.
                    - size depending on the platform, windows -> 24 if not defined, linux const / 15mb
                - will RUN forever, and await database notifications.
                - loops through the Registry.registries -> expect K = db_name, V = registry.
                - if its registry.ready run `ir_cron._process_jobs(db_name)`
    - while no quit signal recivied , e.g == 0
    - @processlimit() do :
        - just check whichever type cron, or http request, already reached maximum limit defined by config.
        - if yes add to list, if dead, removed from list.
        - if still set the limit_reached_time -> to time or the first time its being reached.
        - else would do None.
    - if limit_reached_time is set, it would do (a). check if there is any valid request,
    - if true, will wait abit, time.sleep(1).
    - but if the wait.time already exceed SLEEP_INTERVAL (60s) it would do self.reload
    - (b). if do not have any valid request, it would reload() immediately
    - @reload() do :
        - os.kill(pid, signal.SIGHUP)
        - this process will be continued by the @signal_handler() on SIGHUP event.
    - @stop() do :
        - closing everything and do cleanup.


@ThreadedWSGIServerReloadable do :
    - Inheriting & PATCHING : 
        - (a). LoggingBaseWSGIServerMixIn -> which to only logged output handler.
        - (b). raw werkzeug.serving.ThreadedWSGIServer
    - on Init, it would:
        - set thread maximum limit IF set on environment global value using `INPHMS_MAX_HTTP_THREADS`
        - if not set, its bypassed.
        - then call the raw werkzeug `ThreadedWSGIServer` __init__, and passing:
            - (a). self.host -> self.interface -> 0.0.0.0 -> accept any network conection
            - (b). self.port -> port -> 8069 default
            - (c). self.app -> inphms.http.root = Application()
            - (d). handler -> RequestHandler() class
        - set self.daemon_threads = False, so server can wait when shutting down gracefully.
    - Parent werkzeug patching method.
        - @server_bind():
            - will use systemd existing socket IF available, mostly on linux.
            - else, would just use the werkzeug @server_bind() method.
        - @server_activate():
            - just listen() <- to socket
        - @_handle_request_noblock() :
            - this is patching werkzeug on @serve_forever(), but would call the werkzeug method back again.
            - we would only want to check if Semaphore().acquire(timeout=0.1) means that, if the limit is not yet full, if it is, it would do early return. do possibly do the rest to werkzeug method @serve_forever()
            - else we wanna call the super()._handle_request_noblock()
        - @process_request() :
            - patching the werkzeug /-> overriden by their own ThreadMixin.
            - to add attribute of t.type = 'http' and t.start_time = time.time()
            - full patch. but stil call the self.process_request_thread() as the target.
            - @process_request_thread() do :
                - @finish_request() which will do :
                    - self.RequestHandlerClass() instance creation, <- this is our @RequestHandler class.
        - @shutdown_request() :
            - overriding the werkzeug, but not full.
            - would do Semaphore().release() first.
        - @LoggingBaseWSGIServerMixIn.handle_error() :
            - completely overriding the werkzeug to handle loging error ourselves.


@RequestHandler() do :
    - 

# CONFIG

- will handle all the parsing args CLI + options.
- saved inside `inphms.conf` or `*.conf`

uses `optparse` python library.
will handle parsing command-line arguments like `--port 8080`.

uses optionClass of `@MyOption` so its now default attributes. without overiding the config file values.

