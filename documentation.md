
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
    - 

# CONFIG

- will handle all the parsing args CLI + options.
- saved inside `inphms.conf` or `*.conf`

uses `optparse` python library.
will handle parsing command-line arguments like `--port 8080`.

uses optionClass of `@MyOption` so its now default attributes. without overiding the config file values.

