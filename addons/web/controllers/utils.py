# Part of Inphms, see License file for full copyright and licensing details.

import collections
import logging

import babel.messages.pofile
import werkzeug
import werkzeug.exceptions
import werkzeug.utils
import werkzeug.wrappers
import werkzeug.wsgi
from werkzeug.urls import iri_to_uri

from inphms.tools.translate import JAVASCRIPT_TRANSLATION_COMMENT
from inphms.tools.misc import file_open
from inphms import http
from inphms.http import request


_logger = logging.getLogger(__name__)

def ensure_db(redirect='/web/database/selector', db=None):
    # This helper should be used in web client auth="none" routes
    # if those routes needs a db to work with.
    # If the heuristics does not find any database, then the users will be
    # redirected to db selector or any url specified by `redirect` argument.
    # If the db is taken out of a query parameter, it will be checked against
    # `http.db_filter()` in order to ensure it's legit and thus avoid db
    # forgering that could lead to xss attacks.
    if db is None:
        db = request.params.get('db') and request.params.get('db').strip()

    # Ensure db is legit
    if db and db not in http.db_filter([db]):
        db = None

    if db and not request.session.db:
        # User asked a specific database on a new session.
        # That mean the nodb router has been used to find the route
        # Depending on installed module in the database, the rendering of the page
        # may depend on data injected by the database route dispatcher.
        # Thus, we redirect the user to the same page but with the session cookie set.
        # This will force using the database route dispatcher...
        r = request.httprequest
        url_redirect = werkzeug.urls.url_parse(r.base_url)
        if r.query_string:
            # in P3, request.query_string is bytes, the rest is text, can't mix them
            query_string = iri_to_uri(r.query_string.decode())
            url_redirect = url_redirect.replace(query=query_string)
        request.session.db = db
        werkzeug.exceptions.abort(request.redirect(url_redirect.to_url(), 302))

    # if db not provided, use the session one
    if not db and request.session.db and http.db_filter([request.session.db]):
        db = request.session.db

    # if no database provided and no database in session, use monodb
    if not db:
        all_dbs = http.db_list(force=True)
        if len(all_dbs) == 1:
            db = all_dbs[0]

    # if no db can be found til here, send to the database selector
    # the database selector will redirect to database manager if needed
    if not db:
        werkzeug.exceptions.abort(request.redirect(redirect, 303))

    # always switch the session to the computed db
    if db != request.session.db:
        request.session = http.root.session_store.new()
        request.session.update(http.get_default_session(), db=db)
        request.session.context['lang'] = request.default_lang()
        werkzeug.exceptions.abort(request.redirect(request.httprequest.url, 302))

def is_user_internal(uid):
    return request.env['res.users'].browse(uid)._is_internal()