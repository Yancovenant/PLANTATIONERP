# Part of Inphms, see License file for full copyright and licensing details.

""" Library-wide config variables.

For now, config code is in inphms.tools.config. Its mainly
unprocessed form. The aim is to have code related config in
this module and provide real Python vars.

To init properly this module, inphms.tools.config.parse_config()
MUST BE USED.

"""

addons_path = []

server_wide_modules = []