# Part of Inphms, see License file for full copyright and licensing details.
r"""\
Inphms HTTP layer / WSGI application

The main duty of this module is to prepare and dispatch all http
requests to their corresponding controllers: from a raw http request
arriving on the WSGI entrypoint to a :class:`~http.Request`: arriving at
a module controller with a fully setup ORM available.

Application developers mostly know this module thanks to the
:class:`~inphms.http.Controller`: class and its companion the
:func:`~inphms.http.route`: method decorator. Together they are used to
register methods responsible of delivering web content to matching URLS.

Those two are only the tip of the iceberg, below is a call graph that
shows the various processing layers each request passes through before
ending at the @route decorated endpoint. Hopefully, this call graph and
the attached function descriptions will help you understand this module.

Here be dragons:

"""

# =========================================================
# WSGI Entry Point
# =========================================================
class Application:
    """ INPHMS WSGI Application """
    # See also: https://www.python.org/dev/peps/pep-3333

    

root = Application()