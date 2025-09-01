# -*- coding: utf-8 -*-
# Part of Inphms, see License file for full copyright and licensing details.

import inphms
import inphms.exceptions
from inphms.modules.registry import Registry

def check_session(session, env, request=None):
    self = env['res.users'].browse(session.uid)
    expected = self._compute_session_token(session.sid)
    if expected and inphms.tools.misc.consteq(expected, session.session_token):
        if request:
            env['res.device.log']._update_device(request)
        return True
    return False

def compute_session_token(session, env):
    self = env['res.users'].browse(session.uid)
    return self._compute_session_token(session.sid)