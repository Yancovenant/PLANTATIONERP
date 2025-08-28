# Project Structure - Call Chain Documentation

## Quick Start

Run the application using:
```bash
python inphms-bin
# or with configuration parameters
python inphms-bin --<config-params>
```

---

#### Entry Point
- `inphms.cli.main()`
    - if `--addons-path` handle using:
        - `inphms.tools.config._parse_config(args[0])` **@see config.py**
    - `command = "server"` default command
    - if `args` is not starts with `-`, then it is a command, do:
        - `initialize_sys_path()` **@see module.py**
    - `commands[command]().run(args)` creating instance + `.run()`
    - ---
    - continue as `server` command.
    - ---
    - `inphms.cli.server`
        - `run()`:
            - `inphms.tools.config.parser.prog = f'{Path(sys.argv[0]).name} {self.name}'` decorator / cosmetics **@see config.py**
            - `main(args)`:
                - `check_root_user()`
                - `inphms.tools.config.parse_config(args, setup_logging=True)`
                - `check_postgres_user()`:
                    - `config = inphms.tools.config` **@see config.py**
                - `report_configuration()`
                    - `config = inphms.tools.config` **@see config.py**
                - `setup_pid_file()`:
                    - `config = inphms.tools.config` **@see config.py**
                    - at exit, `rm_pid_file(main_pid)`
                - `rc = inphms.service.server.start(preload=preload, stop=stop)`
                    - `load_server_wide_modules()`:
                        - `load_inphms_module(m)` **@see module.py**
                    - ---
                    - continue as `windows/nt` platform
                    - ---
                    - `server = ThreadedServer(inphms.http.root)`
                        - `inphms.http.root = Application()` **@see http.py**
                        - `ThreadedServer.__init__(inphms.http.root)`:
                            - `CommonServer.__init__(app)`:
                                - self.app = app <- inphms.http.root,
                                - self.interface = config['http_interface'] or '0.0.0.0'
                                - self.port = config['http_port']
                                - self.pid = os.getpid()
                            - self.main_thread_id = threading.current_thread().ident
                            - self.quit_signals_received = 0
                            - self.httpd = None
                            - self.limits_reached_threads = set()
                            - self.limit_reached_time = None
                    - `rc = server.run(preload, stop) == ThreadedServer.run()`
                        - `self.start(stop=stop)`:
                            - `set_limit_memory_hard()`
                            - `self.http_spawn()`
                                - self.httpd = ThreadedWSGIServerReloadable(self.interface, self.port, self.app)
                                    - `ThreadedWSGIServerReloadable(LoggingBaseWSGIServerMixIn, werkzeug.serving.ThreadedWSGIServer):`
                                        - `__init__(self, interface, port, app):`
                                            - self.max_http_threads = os.environ.get("INPHMS_MAX_HTTP_THREADS")
                                            - if self.max_http_threads:
                                                - self.http_threads_sem = threading.Semaphore(self.max_http_threads)
                                            - `super().__init__(host, port, app, handler=RequestHandler)`:
                                                - RequestHandler.protocol_version = "HTTP/1.1" if self.multithread or self.multiprocess
                                                - self.host = host
                                                - self.port = port
                                                - self.app = app
                                                - self.passthrough_errors = passthrough_errors = False (default)
                                                - self.address_family = select_address_family(host, port)
                                                - server_address = get_sockaddr(host, int(port), address_family)
                                                - `super().__init__(server_address, handler, bind_and_activate=False)`
                                                    - `BaseServer.__init__(self, server_address, RequestHandlerClass)`:
                                                        - self.server_address = server_address
                                                        - self.RequestHandlerClass = RequestHandlerClass == RequestHandler
                                                        - self.__is_shut_down = threading.Event()
                                                        - self.__shutdown_request = False
                                                    - self.socket = socket.socket(self.address_family, self.socket_type)
                                                - `self.server_bind()`:
                                                    - ThreadedWSGIServerReloadable.server_bind():
                                                        - self.reload_socket = Bool
                                                        - self.socket, reasign if.
                                                        - super().server_bind()
                                                            - `TCPServer.server_bind():`
                                                                - self.socket.bind(self.server_address)
                                                                - self.server_address = self.socket.getsockname()
                                                            - host, port = self.server_address[:2]
                                                            - self.server_name = socket.getfqdn(host) // fully qualified domain name
                                                            - self.server_port = port
                                                - `self.server_activate()`
                                                    - ThreadedWSGIServerReloadable.server_activate():
                                                        - if not self.reload_socket:
                                                            - super().server_activate()
                                                                - TCPServer.server_activate():
                                                                    - self.socket.listen(self.request_queue_size)
                                                - `Except error`:
                                                    - self.server_close():
                                                        -
                                                

                                            - self.daemon_threads = False
    
- `config = configmanager() == inphms.tools.config`