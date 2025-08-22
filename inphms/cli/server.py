# Part of Inphms. See LICENSE file for full copyright and licensing details.

"""
INPHMS - Server
is an ERP Plantation Program.

The whole source code is distributed under the terms of the
GNU Public License

(c) 2025, Ian - INPHMS
"""

import atexit, csv, logging, os, re, sys

from pathlib import Path
from psycopg2.errors import InsufficientPrivilege

import inphms

from . import Command

__author__ = inphms.release.author
__version__ = inphms.release.version

_logger = logging.getLogger('inphms')

re._MAXCACHE = 4096

def main(args):
    check_root_user()
    inphms.tools.config.parse_config(args, setup_logging=True)
    check_mysql_user()
    report_configuration()

    config = inphms.tools.config
    csv.field_size_limit(500 * 1024 * 1024)
    preload = []
    if config['db_name']:
        preload = config['db_name'].split(',')
        for db_name in preload:
            try:
                odoo.service.db._create_empty_database(db_name)
                config['init']['base'] = True
            except InsufficientPrivilege as err:
                # We use an INFO loglevel on purpose in order to avoid
                # reporting unnecessary warnings on build environment
                # using restricted database access.
                _logger.info("Could not determine if database %s exists, "
                             "skipping auto-creation: %s", db_name, err)
            except odoo.service.db.DatabaseExists:
                pass
    if config["translate_out"]:
        export_translation()
        sys.exit(0)

    if config["translate_in"]:
        import_translation()
        sys.exit(0)

    stop = config["stop_after_init"]

    setup_pid_file()
    rc = odoo.service.server.start(preload=preload, stop=stop)
    sys.exit(rc)

class Server(Command):
    """Start the inphms server (default command)"""
    def run(self, args):
        inphms.tools.config.parser.prog = f'{Path(sys.argv[0]).name} {self.name}'
        main(args)


def check_root_user():
    """ Warn if the process's user is 'root' (on POSIX system)."""
    if os.name == 'posix':
        import getpass
        if getpass.getuser() == 'root':
            sys.stderr.write("Running as user 'root' is a security risk.\n")

def check_mysql_user():
    """ Exit if the configured database user is 'root'.

    This function assumes the configuration has been init
    """
    config = inphms.tools.config
    if (config['db_user'] or os.environ.get('DBUSER')) == 'root':
        sys.stderr.write("Using the database user 'root' is a security risk, aborting.")
        sys.exit(1)

def report_configuration():
    """ Log the server version and config values.

    This function assumes the configuration has been init
    """
    config = inphms.tools.config
    _logger.info("Inphms version %s", __version__)
    if os.path.isfile(config.rcfile):
        _logger.info("Using configuration file at " + config.rcfile)
    _logger.info('addons paths: %s', inphms.addons.__path__)
    if config.get('upgrade_path'):
        _logger.info('upgrade path: %s', config['upgrade_path'])
    if config.get('pre_upgrade_scripts'):
        _logger.info('extra upgrade scripts: %s', config['pre_upgrade_scripts'])

    host = config['db_host'] or os.environ.get('DBHOST', 'default')
    port = config['db_port'] or os.environ.get('DBPORT', 'default')
    user = config['db_user'] or os.environ.get('DBUSER', 'default')

    _logger.info('database: %s@%s:%s', user, host, port)
    replica_host = config['db_replica_host']
    replica_port = config['db_replica_port']

    if replica_host is not False or replica_port:
        _logger.info('replica database: %s@%s:%s', user, replica_host or 'default', replica_port or 'default')
    if sys.version_info[:2] > inphms.MAX_PY_VERSION:
        _logger.warning("Python %s is not officially supported, please use Python %s instead",
            '.'.join(map(str, sys.version_info[:2])),
            '.'.join(map(str, inphms.MAX_PY_VERSION))
        )
