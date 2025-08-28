# -*- coding: utf-8 -*-
import base64
import json
import logging
import os
import shutil
import subprocess
import tempfile
import zipfile
from contextlib import closing
from xml.etree import ElementTree as ET

import psycopg2
from psycopg2.extensions import quote_ident
from decorator import decorator
from pytz import country_timezones

import inphms
import inphms.release
import inphms.sql_db
import inphms.tools
from inphms import SUPERUSER_ID
# from inphms.exceptions import AccessDenied
from inphms.release import version_info
from inphms.sql_db import db_connect
from inphms.tools import SQL
# from inphms.tools.misc import exec_pg_environ, find_pg_tool

_logger = logging.getLogger(__name__)


def list_dbs(force=False):
    if not inphms.tools.config['list_db'] and not force:
        raise inphms.exceptions.AccessDenied()

    if not inphms.tools.config['dbfilter'] and inphms.tools.config['db_name']:
        # In case --db-filter is not provided and --database is passed, Inphms will not
        # fetch the list of databases available on the postgres server and instead will
        # use the value of --database as comma seperated list of exposed databases.
        res = sorted(db.strip() for db in inphms.tools.config['db_name'].split(','))
        return res

    chosen_template = inphms.tools.config['db_template']
    templates_list = tuple({'postgres', chosen_template})
    db = inphms.sql_db.db_connect('postgres')
    with closing(db.cursor()) as cr:
        try:
            cr.execute("""
                SELECT datname FROM pg_database
                WHERE datdba=(SELECT usesysid FROM pg_user WHERE usename=current_user)
                AND NOT datistemplate AND datallowconn 
                AND datname NOT IN %s 
                ORDER BY datname
            """, (templates_list,))
            return [name for (name,) in cr.fetchall()]
        except Exception:
            _logger.exception('Listing databases failed:')
            return []




