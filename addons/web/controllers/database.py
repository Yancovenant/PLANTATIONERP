# Part of Inphms, see License file for full copyright and licensing details.

import datetime
import logging
import os
import re
import tempfile

from lxml import html

import inphms
import inphms.modules.registry
from inphms import http
# from inphms.http import content_disposition, dispatch_rpc, request, Response
from inphms.http import dispatch_rpc
from inphms.http import request
from inphms.service import db
# from inphms.tools.misc import file_open, str2bool
from inphms.tools.misc import file_open
# from inphms.tools.translate import _

from inphms.addons.base.models.ir_qweb import render as qweb_render


_logger = logging.getLogger(__name__)


DBNAME_PATTERN = '^[a-zA-Z0-9][a-zA-Z0-9_.-]+$'

class Database(http.Controller):

    def _render_template(self, **d):
        d.setdefault('manage', True)
        d['insecure'] = inphms.tools.config.verify_admin_password('admin')
        d['list_db'] = inphms.tools.config['list_db']
        d['langs'] = inphms.service.db.exp_list_lang()
        d['countries'] = inphms.service.db.exp_list_countries()
        d['pattern'] = DBNAME_PATTERN
        # databases list
        try:
            d['databases'] = http.db_list()
            d['incompatible_databases'] = inphms.service.db.list_db_incompatible(d['databases'])
        except inphms.exceptions.AccessDenied:
            d['databases'] = [request.db] if request.db else []

        templates = {}
        with file_open("web/static/src/public/database_manager.qweb.html", "r") as fd:
            templates['database_manager'] = fd.read()
        with file_open("web/static/src/public/database_manager.master_input.qweb.html", "r") as fd:
            templates['master_input'] = fd.read()
        with file_open("web/static/src/public/database_manager.create_form.qweb.html", "r") as fd:
            templates['create_form'] = fd.read()
        
        def load(template_name):
            fromstring = html.document_fromstring if template_name == 'database_manager' else html.fragment_fromstring
            return (fromstring(templates[template_name]), template_name)
    
        return qweb_render('database_manager', d, load)

    @http.route('/web/database/selector', type='http', auth="none")
    def selector(self, **kw):
        if request.db:
            request.env.cr.close()
        return self._render_template(manage=False)

    @http.route('/web/database/create', type='http', auth="none", methods=['POST'], csrf=False)
    def create(self, master_pwd, name, lang, password, **post):
        insecure = inphms.tools.config.verify_admin_password('admin')
        if insecure and master_pwd:
            dispatch_rpc('db', 'change_admin_password', ["admin", master_pwd])
        try:
            if not re.match(DBNAME_PATTERN, name):
                raise Exception(_('Houston, we have a database naming issue! Make sure you only use letters, numbers, underscores, hyphens, or dots in the database name, and you\'ll be golden.'))
            # country code could be = "False" which is actually True in python
            country_code = post.get('country_code') or False
            dispatch_rpc('db', 'create_database', [master_pwd, name, bool(post.get('demo')), lang, password, post['login'], country_code, post['phone']])
            credential = {'login': post['login'], 'password': password, 'type': 'password'}
            request.session.authenticate(name, credential)
            request.session.db = name
            return request.redirect('/inphms')
        except Exception as e:
            _logger.exception("Database creation error.")
            error = "Database creation error: %s" % (str(e) or repr(e))
        return self._render_template(error=error)