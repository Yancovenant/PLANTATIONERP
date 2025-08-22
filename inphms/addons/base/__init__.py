# Part of Inphms, see License file for full copyright and licensing details.

from . import controllers

def post_init(env):
    """Rewrite ICP's to force groups"""
    env['ir.config_parameter'].init(force=True)
