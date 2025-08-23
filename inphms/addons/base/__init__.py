# Part of Inphms, see License file for full copyright and licensing details.

from . import controllers
from . import models

def post_init(env):
    """Rewrite ICP's to force groups"""
    env['ir.config_parameter'].init(force=True)
