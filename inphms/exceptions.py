# -*- coding: utf-8 -*-
# Part of Inphms, see License file for full copyright and licensing details.


"""The Inphms Exceptions module defines a few core exception types.

Those types are understood by the RPC layer.
Any other exception type bubbling until the RPC layer will be
treated as a 'Server error'.

.. note::
    If you consider introducing new exceptions,
    check out the :mod:`inphms.addons.test_exceptions` module.
"""


class UserError(Exception):
    """Generic error managed by the client.

    Typically when the user tries to do something that has no sense given the current
    state of a record. Semantically comparable to the generic 400 HTTP status codes.
    """

    def __init__(self, message):
        """
        :param message: exception message and frontend modal content
        """
        super().__init__(message)

class AccessError(UserError):
    """Access rights error.

    .. admonition:: Example

        When you try to read a record that you are not allowed to.
    """

class AccessDenied(UserError):
    """Login/password error.

    .. note::

        No traceback.

    .. admonition:: Example

        When you try to log with a wrong password.
    """

    def __init__(self, message="Access Denied"):
        super().__init__(message)
        self.with_traceback(None)
        self.__cause__ = None
        self.traceback = ('', '', '')

class ValidationError(UserError):
    """Violation of python constraints.

    .. admonition:: Example

        When you try to create a new user with a login which already exist in the db.
    """