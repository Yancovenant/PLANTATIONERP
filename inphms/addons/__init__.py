# Part of Inphms, see License file for full copyright and licensing details.

""" Addons module.

This module serves to contain all Inphms addons, across all configured addons
paths. For the code to manage those addons, see inphms.modules.

Addons are made available under `inphms.addons` after
inphms.tools.config.parse_config() is called (so that the addons paths are
known).

This module also conveniently reexports some symbols from inphms.modules.
Importing them from here is deprecated.

"""

import pkgutil
import os.path
__path__ = [
    os.path.abspath(path)
    for path in pkgutil.extend_path(__path__, __name__)
]