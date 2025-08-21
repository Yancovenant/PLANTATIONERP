# Part of Inphms, see License file for full copyright and licensing details.

import configparser as configparser
import errno
import logging
import optparse
import glob
import os
import sys
import tempfile
import warnings

import inphms

from os.path import expandvars, expanduser, abspath, realpath, normcase
from .. import release, conf, loglevels
from . import appdirs

from passlib.context import CryptContext
crypt_context = CryptContext(schemes=['pbkdf2_sha512', 'plaintext'],
                             deprecated=['plaintext'],
                             pbkdf2_sha512__rounds=600_000)

class MyOption(optparse.Option, object):
    """ optparse Option with two additional attributes.

    The list of command line options (getopt.Option) is used to create the
    list of the configuration file options. When reading the file, and then
    reading the command line arguments, we don't want optparse.parse results
    to override the configuration file values. But if we provide default
    values to optparse, optparse will return them and we can't know if they
    were really provided by the user or not. A solution is to not use
    optparse's default attribute, but use a custom one (that will be copied
    to create the default values of the configuration file).

    """
    def __init__(self, *opt, **attrs):
        self.my_default = attrs.pop('my_default', None)
        super(MyOption, self).__init__(*opt, **attrs)

class configmanager(object):
    def __init__(self, fname=None):
        """Constructor.

        :param fname: a shortcut allowing to instantiate :class:`configmanager`
                      from Python code without resorting to env variable
        """

        self.options = {
            'admin_pwd': 'supervisor',
            'root_path': None,
        }

        self.config_file = fname

        self._LOGLEVELS = dict([
            (getattr(loglevels, 'LOG_%s' % x), getattr(logging, x))
            for x in ('CRITICAL', 'ERROR', 'WARNING', 'INFO', 'DEBUG', 'NOTSET')
        ])

        version = "%s %s" % (release.description, release.version)
        self.parser = parser = optparse.OptionParser(version=version, option_class=MyOption)
    
        self._parse_config()

    def parse_config(self, args: list[str] | None = None, *, setup_logging: bool | None = None) -> None:
        """ Parse the configuration file (if any) and the cli arguments.

        This function init inphms.tools.config and inphms.conf

        this method must be called before proper usage of this lib can be made.
        
        Typical usage of this function:

            inphms.tools.config.parse_config(sys.argv[1:])
        """
        opt = self._parse_config(args)
        if setup_logging is not False:
            inphms.netsvc.init_logger()
            if setup_logging is None:
                warnings.warn(
                    "It's recommended to specify wheter"
                    " you want Inphms to setup its own logging"
                    " (or want to handle it yourself)",
                    category=PendingDeprecationWarning,
                    stacklevel=2,
                )
        self._warn_deprecated_options()
        inphms.modules.module.initialize_sys_path()
        return opt

    def _parse_config(self, args=None):
        if args is None:
            args = []
        opt, args = self.parser.parse_args(args)
    

    def _warn_deprecated_options(self):
        for old_option_name, new_option_name in [
            ('geoip_database', 'geoip_city_db'),
            ('osv_memory_age_limit', 'transient_age_limit')
        ]:
            deprecated_value = self.options.pop(old_option_name, None)
            if deprecated_value:
                default_value = self.casts[new_option_name].my_default
                current_value = self.options[new_option_name]

config = configmanager()