
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
- individually import and append to sys.modules and run post_load function, to do some setup if defined.
- being read by __manifest__.py
- setup the correct Server Class, ThreadedServer + APP => inphms.http.root mostly and .run() it.
    - the groundwork would be something like this:
        a. server (ThreadedServer) // server is serving
        b. app (Application, inphms.http.root) // application
        c. client (Webserver) // to customer a.k.a web browsers
    

@ThreadedServer do:
    - run() :
        - will use registry.lock to self.start() the server.
    - start() :
        - will set memory limit, ONLY FOR LINUX
        - Setting up signal to be used by signal_handler
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

# CONFIG

- will handle all the parsing args CLI + options.
- saved inside `inphms.conf` or `*.conf`

uses `optparse` python library.
will handle parsing command-line arguments like `--port 8080`.

uses optionClass of `@MyOption` so its now default attributes. without overiding the config file values.

