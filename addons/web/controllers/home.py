# Part of Inphms, see License file for full copyright and licensing details.

import json
import logging
import psycopg2

from inphms import http
from inphms.http import request

_logger = logging.getLogger(__name__)

class Home(http.Controller):
    @http.route('/', type='http', auth='none')
    def index(self, s_action=None, db=None, **kw):
        # if request.db and request.session.uid:
        #     return request.redirect_query('/web/login_successful', query=request.params)
        # return request.redirect_query('/inphms', query=request.params)
        return "Hello World"