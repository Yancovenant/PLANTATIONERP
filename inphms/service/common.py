# -*- coding: utf-8 -*-

import logging

import inphms.release
import inphms.tools
from inphms.exceptions import AccessDenied
from inphms.modules.registry import Registry
from inphms.tools.translate import _

_logger = logging.getLogger(__name__)

RPC_VERSION_1 = {
        'server_version': inphms.release.version,
        'server_version_info': inphms.release.version_info,
        'server_serie': inphms.release.serie,
        'protocol_version': 1,
}


def dispatch(method, params):
    g = globals()
    exp_method_name = 'exp_' + method
    if exp_method_name in g:
        return g[exp_method_name](*params)
    else:
        raise Exception("Method not found: %s" % method)