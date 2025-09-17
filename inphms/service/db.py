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
from inphms.exceptions import AccessDenied
from inphms.release import version_info
from inphms.sql_db import db_connect
from inphms.tools import SQL
# from inphms.tools.misc import exec_pg_environ, find_pg_tool

_logger = logging.getLogger(__name__)


class DatabaseExists(Warning):
    pass

def database_identifier(cr, name: str) -> SQL:
    """Quote a database identifier.

    Use instead of `SQL.identifier` to accept all kinds of identifiers.
    """
    name = quote_ident(name, cr._cnx)
    return SQL(name)

def check_db_management_enabled(method):
    def if_db_mgt_enabled(method, self, *args, **kwargs):
        if not inphms.tools.config['list_db']:
            _logger.error('Database management functions blocked, admin disabled database listing')
            raise AccessDenied()
        return method(self, *args, **kwargs)
    return decorator(if_db_mgt_enabled, method)


#----------------------------------------------------------
# Master password required
#----------------------------------------------------------

def check_super(passwd):
    if passwd and inphms.tools.config.verify_admin_password(passwd):
        return True
    raise inphms.exceptions.AccessDenied()

# This should be moved to odoo.modules.db, along side initialize().
def _initialize_db(id, db_name, demo, lang, user_password, login='admin', country_code=None, phone=None):
    try:
        db = inphms.sql_db.db_connect(db_name)
        with closing(db.cursor()) as cr:
            # TODO this should be removed as it is done by Registry.new().
            inphms.modules.db.initialize(cr)
            inphms.tools.config['load_language'] = lang
            cr.commit()

        registry = inphms.modules.registry.Registry.new(db_name, demo, None, update_module=True)

        with closing(registry.cursor()) as cr:
            env = inphms.api.Environment(cr, SUPERUSER_ID, {})

            if lang:
                modules = env['ir.module.module'].search([('state', '=', 'installed')])
                modules._update_translations(lang)

            if country_code:
                country = env['res.country'].search([('code', 'ilike', country_code)])[0]
                env['res.company'].browse(1).write({'country_id': country_code and country.id, 'currency_id': country_code and country.currency_id.id})
                if len(country_timezones.get(country_code, [])) == 1:
                    users = env['res.users'].search([])
                    users.write({'tz': country_timezones[country_code][0]})
            if phone:
                env['res.company'].browse(1).write({'phone': phone})
            if '@' in login:
                env['res.company'].browse(1).write({'email': login})

            # update admin's password and lang and login
            values = {'password': user_password, 'lang': lang}
            if login:
                values['login'] = login
                emails = odoo.tools.email_split(login)
                if emails:
                    values['email'] = emails[0]
            env.ref('base.user_admin').write(values)

            cr.commit()
    except Exception as e:
        _logger.exception('CREATE DATABASE failed:')

def _check_faketime_mode(db_name):
    if os.getenv('INPHMS_FAKETIME_TEST_MODE') and db_name in inphms.tools.config['db_name'].split(','):
        try:
            db = inphms.sql_db.db_connect(db_name)
            with db.cursor() as cursor:
                cursor.execute("SELECT (pg_catalog.now() AT TIME ZONE 'UTC');")
                server_now = cursor.fetchone()[0]
                time_offset = (datetime.now() - server_now).total_seconds()

                cursor.execute("""
                    CREATE OR REPLACE FUNCTION public.now()
                        RETURNS timestamp with time zone AS $$
                            SELECT pg_catalog.now() +  %s * interval '1 second';
                        $$ LANGUAGE sql;
                """, (int(time_offset), ))
                cursor.execute("SELECT (now() AT TIME ZONE 'UTC');")
                new_now = cursor.fetchone()[0]
                _logger.info("Faketime mode, new cursor now is %s", new_now)
                cursor.commit()
        except psycopg2.Error as e:
            _logger.warning("Unable to set fakedtimed NOW() : %s", e)

def _create_empty_database(name):
    db = inphms.sql_db.db_connect('postgres')
    with closing(db.cursor()) as cr:
        chosen_template = inphms.tools.config['db_template']
        cr.execute("SELECT datname FROM pg_database WHERE datname = %s",
                   (name,), log_exceptions=False)
        if cr.fetchall():
            _check_faketime_mode(name)
            raise DatabaseExists("database %r already exists!" % (name,))
        else:
            # database-altering operations cannot be executed inside a transaction
            cr.rollback()
            cr._cnx.autocommit = True

            # 'C' collate is only safe with template0, but provides more useful indexes
            cr.execute(SQL(
                "CREATE DATABASE %s ENCODING 'unicode' %s TEMPLATE %s",
                database_identifier(cr, name),
                SQL("LC_COLLATE 'C'") if chosen_template == 'template0' else SQL(""),
                database_identifier(cr, chosen_template),
            ))

    # TODO: add --extension=trigram,unaccent
    try:
        db = inphms.sql_db.db_connect(name)
        with db.cursor() as cr:
            cr.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
            if inphms.tools.config['unaccent']:
                cr.execute("CREATE EXTENSION IF NOT EXISTS unaccent")
                # From PostgreSQL's point of view, making 'unaccent' immutable is incorrect
                # because it depends on external data - see
                # https://www.postgresql.org/message-id/flat/201012021544.oB2FiTn1041521@wwwmaster.postgresql.org#201012021544.oB2FiTn1041521@wwwmaster.postgresql.org
                # But in the case of Odoo, we consider that those data don't
                # change in the lifetime of a database. If they do change, all
                # indexes created with this function become corrupted!
                cr.execute("ALTER FUNCTION unaccent(text) IMMUTABLE")
    except psycopg2.Error as e:
        _logger.warning("Unable to create PostgreSQL extensions : %s", e)
    _check_faketime_mode(name)

    # restore legacy behaviour on pg15+
    try:
        db = inphms.sql_db.db_connect(name)
        with db.cursor() as cr:
            cr.execute("GRANT CREATE ON SCHEMA PUBLIC TO PUBLIC")
    except psycopg2.Error as e:
        _logger.warning("Unable to make public schema public-accessible: %s", e)

@check_db_management_enabled
def exp_create_database(db_name, demo, lang, user_password='admin', login='admin', country_code=None, phone=None):
    """ Similar to exp_create but blocking."""
    _logger.info('Create database `%s`.', db_name)
    _create_empty_database(db_name)
    _initialize_db(id, db_name, demo, lang, user_password, login, country_code, phone)
    return True

@check_db_management_enabled
def exp_change_admin_password(new_password):
    inphms.tools.config.set_admin_password(new_password)
    inphms.tools.config.save(['admin_passwd'])
    return True

#----------------------------------------------------------
# No master password required
#----------------------------------------------------------

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

#----------------------------------------------------------
# db service dispatch
#----------------------------------------------------------

def dispatch(method, params):
    g = globals()
    exp_method_name = 'exp_' + method
    if method in ['db_exist', 'list', 'list_lang', 'server_version']:
        return g[exp_method_name](*params)
    elif exp_method_name in g:
        passwd = params[0]
        params = params[1:]
        check_super(passwd)
        return g[exp_method_name](*params)
    else:
        raise KeyError("Method not found: %s" % method)
