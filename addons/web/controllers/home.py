# Part of Inphms, see License file for full copyright and licensing details.

import json
import logging
import psycopg2

import inphms.exceptions
import inphms.modules.registry
from inphms import http
from inphms.exceptions import AccessError
from inphms.http import request
from inphms.service import security
# from inphms.tools.translate import _
from .utils import (
    ensure_db,
#     _get_login_redirect_url,
    is_user_internal,
)

_logger = logging.getLogger(__name__)

# Shared parameters for all login/signup flows
SIGN_UP_REQUEST_PARAMS = {'db', 'login', 'debug', 'token', 'message', 'error', 'scope', 'mode',
                          'redirect', 'redirect_hostname', 'email', 'name', 'partner_id',
                          'password', 'confirm_password', 'city', 'country_id', 'lang', 'signup_email'}
LOGIN_SUCCESSFUL_PARAMS = set()
CREDENTIAL_PARAMS = ['login', 'password', 'type']

class Home(http.Controller):

    @http.route('/', type='http', auth='none')
    def index(self, s_action=None, db=None, **kw):
        if request.db and request.session.uid:
            return request.redirect_query('/web/login_successful', query=request.params)
        return request.redirect_query('/inphms', query=request.params)
    
    def _web_client_readonly(self):
        return False

    # ideally, this route should be `auth="user"` but that don't work in non-monodb mode.
    @http.route(['/web', '/inphms', '/inphms/<path:subpath>', '/scoped_app/<path:subpath>'], type='http', auth="none", readonly=_web_client_readonly)
    def web_client(self, s_action=None, **kw):
        # Ensure we have both a database and a user
        ensure_db()
        if not request.session.uid:
            return request.redirect_query('/web/login', query={'redirect': request.httprequest.full_path}, code=303)
        if kw.get('redirect'):
            return request.redirect(kw.get('redirect'), 303)
        if not security.check_session(request.session, request.env, request):
            raise http.SessionExpiredException("Session expired")
        if not is_user_internal(request.session.uid):
            return request.redirect('/web/login_successful', 303)

        # Side-effect, refresh the session lifetime
        request.session.touch()

        # Restore the user on the environment, it was lost due to auth="none"
        request.update_env(user=request.session.uid)
        try:
            if request.env.user:
                request.env.user._on_webclient_bootstrap()
            context = request.env['ir.http'].webclient_rendering_context()
            response = request.render('web.webclient_bootstrap', qcontext=context)
            response.headers['X-Frame-Options'] = 'DENY'
            return response
        except AccessError:
            return request.redirect('/web/login?error=access')