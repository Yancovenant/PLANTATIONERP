# -*- coding: utf-8 -*-
# Part of Inphms, see License file for full copyright and licensing details.

""" Modules (also called Addons) Management

"""

from . import module
from . import registry

from inphms.modules.module import (
    initialize_sys_path,
    get_modules,
    get_module_path,
    load_inphms_module,
    get_manifest,
    adapt_version,
)