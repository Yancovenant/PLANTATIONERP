# Part of Inphms, see License file for full copyright and licensing details.

import logging

import inphms
from inphms.tools import config

_logger = logging.getLogger(__name__)

SLEEP_INTERVAL = 60

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
        print("else on server start")

server = None
server_phoenix = False
def load_server_wide_modules():
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