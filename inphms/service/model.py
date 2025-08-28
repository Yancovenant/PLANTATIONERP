# Part of Inphms, see License file for full copyright and licensing details.
import logging
import random
import threading
import time
from collections.abc import Mapping, Sequence
from functools import partial

from psycopg2 import IntegrityError, OperationalError, errorcodes, errors

import inphms
from inphms.exceptions import UserError, ValidationError, AccessError
# from inphms.models import BaseModel
from inphms.http import request
from inphms.modules.registry import Registry
from inphms.tools import DotDict, lazy
from inphms.tools.translate import translate_sql_constraint

from . import security

_logger = logging.getLogger(__name__)

PG_CONCURRENCY_ERRORS_TO_RETRY = (errorcodes.LOCK_NOT_AVAILABLE, errorcodes.SERIALIZATION_FAILURE, errorcodes.DEADLOCK_DETECTED)
PG_CONCURRENCY_EXCEPTIONS_TO_RETRY = (errors.LockNotAvailable, errors.SerializationFailure, errors.DeadlockDetected)
MAX_TRIES_ON_CONCURRENCY_FAILURE = 5


def _as_validation_error(env, exc):
    """ Return the IntegrityError encapsuled in a nice ValidationError """

    unknown = env._('Unknown')
    model = DotDict({'_name': 'unknown', '_description': unknown})
    field = DotDict({'name': 'unknown', 'string': unknown})
    for _name, rclass in env.registry.items():
        if exc.diag.table_name == rclass._table:
            model = rclass
            field = model._fields.get(exc.diag.column_name) or field
            break

    match exc:
        case errors.NotNullViolation():
            return ValidationError(env._(
                "The operation cannot be completed:\n"
                "- Create/update: a mandatory field is not set.\n"
                "- Delete: another model requires the record being deleted."
                " If possible, archive it instead.\n\n"
                "Model: %(model_name)s (%(model_tech_name)s)\n"
                "Field: %(field_name)s (%(field_tech_name)s)\n",
                model_name=model._description,
                model_tech_name=model._name,
                field_name=field.string,
                field_tech_name=field.name,
            ))

        case errors.ForeignKeyViolation():
            return ValidationError(env._(
                "The operation cannot be completed: another model requires "
                "the record being deleted. If possible, archive it instead.\n\n"
                "Model: %(model_name)s (%(model_tech_name)s)\n"
                "Constraint: %(constraint)s\n",
                model_name=model._description,
                model_tech_name=model._name,
                constraint=exc.diag.constraint_name,
            ))

    if exc.diag.constraint_name in env.registry._sql_constraints:
        return ValidationError(env._(
            "The operation cannot be completed: %s",
            translate_sql_constraint(env.cr, exc.diag.constraint_name, env.context.get('lang', 'en_US'))
        ))

    return ValidationError(env._("The operation cannot be completed: %s", exc.args[0]))

def retrying(func, env):
    """
    Call ``func`` until the function returns without serialisation
    error. A serialisation error occurs when two requests in independent
    cursors perform incompatible changes (such as writing different
    values on a same record). By default, it retries up to 5 times.

    :param callable func: The function to call, you can pass arguments
        using :func:`functools.partial`:.
    :param odoo.api.Environment env: The environment where the registry
        and the cursor are taken.
    """
    try:
        for tryno in range(1, MAX_TRIES_ON_CONCURRENCY_FAILURE + 1):
            tryleft = MAX_TRIES_ON_CONCURRENCY_FAILURE - tryno
            try:
                result = func()
                if not env.cr._closed:
                    env.cr.flush()  # submit the changes to the database
                break
            except (IntegrityError, OperationalError) as exc:
                if env.cr._closed:
                    raise
                env.cr.rollback()
                env.reset()
                env.registry.reset_changes()
                if request:
                    request.session = request._get_session_and_dbname()[0]
                    # Rewind files in case of failure
                    for filename, file in request.httprequest.files.items():
                        if hasattr(file, "seekable") and file.seekable():
                            file.seek(0)
                        else:
                            raise RuntimeError(f"Cannot retry request on input file {filename!r} after serialization failure") from exc
                if isinstance(exc, IntegrityError):
                    raise _as_validation_error(env, exc) from exc
                if not isinstance(exc, PG_CONCURRENCY_EXCEPTIONS_TO_RETRY):
                    raise
                if not tryleft:
                    _logger.info("%s, maximum number of tries reached!", errorcodes.lookup(exc.pgcode))
                    raise

                wait_time = random.uniform(0.0, 2 ** tryno)
                _logger.info("%s, %s tries left, try again in %.04f sec...", errorcodes.lookup(exc.pgcode), tryleft, wait_time)
                time.sleep(wait_time)
        else:
            # handled in the "if not tryleft" case
            raise RuntimeError("unreachable")

    except Exception:
        env.reset()
        env.registry.reset_changes()
        raise

    if not env.cr.closed:
        env.cr.commit()  # effectively commits and execute post-commits
    env.registry.signal_changes()
    return result