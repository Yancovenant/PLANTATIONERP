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
from datetime import datetime
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

def list_db_incompatible(databases): #ichecked
    """"Check a list of databases if they are compatible with this version of Inphms

        :param databases: A list of existing Postgresql databases
        :return: A list of databases that are incompatible
    """
    incompatible_databases = []
    server_version = '.'.join(str(v) for v in version_info[:2])
    for database_name in databases:
        with closing(db_connect(database_name).cursor()) as cr:
            if inphms.tools.sql.table_exists(cr, 'ir_module_module'):
                cr.execute("SELECT latest_version FROM ir_module_module WHERE name=%s", ('base',))
                base_version = cr.fetchone()
                if not base_version or not base_version[0]:
                    incompatible_databases.append(database_name)
                else:
                    # e.g. 10.saas~15
                    local_version = '.'.join(base_version[0].split('.')[:2])
                    if local_version != server_version:
                        incompatible_databases.append(database_name)
            else:
                incompatible_databases.append(database_name)
    for database_name in incompatible_databases:
        # release connection
        inphms.sql_db.close_db(database_name)
    return incompatible_databases

def exp_list_countries(): #ichecked
    list_countries = []
    root = ET.parse(os.path.join(inphms.tools.config['root_path'], 'addons/base/data/res_country_data.xml')).getroot()
    for country in root.find('data').findall('record[@model="res.country"]'):
        name = country.find('field[@name="name"]').text
        code = country.find('field[@name="code"]').text
        list_countries.append([code, name])
    return sorted(list_countries, key=lambda c: c[1])

def exp_list_lang(): #ichecked
    return inphms.tools.misc.scan_languages()

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


