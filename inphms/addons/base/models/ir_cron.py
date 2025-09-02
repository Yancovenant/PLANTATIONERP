# Part of Inphms. See LICENSE file for full copyright and licensing details.
import logging
import threading
import time
import os
import psycopg2
import psycopg2.errors
import pytz
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta

import inphms
from inphms import api, fields, models, _
from inphms.exceptions import UserError
from inphms.modules.registry import Registry
from inphms.tools import SQL

_logger = logging.getLogger(__name__)

class ir_cron(models.Model):
    """ Model describing cron jobs (also called actions or tasks).
    """

    # TODO: perhaps in the future we could consider a flag on ir.cron jobs
    # that would cause database wake-up even if the database has not been
    # loaded yet or was already unloaded (e.g. 'force_db_wakeup' or something)
    # See also inphms.cron

    _name = 'ir.cron'
    _order = 'cron_name'
    _description = 'Scheduled Actions'
    _allow_sudo_commands = False

    ir_actions_server_id = fields.Many2one(
        'ir.actions.server', 'Server action',
        delegate=True, ondelete='restrict', required=True)
    cron_name = fields.Char('Name', compute='_compute_cron_name', store=True)
    user_id = fields.Many2one('res.users', string='Scheduler User', default=lambda self: self.env.user, required=True)
    active = fields.Boolean(default=True)
    interval_number = fields.Integer(default=1, aggregator=None, help="Repeat every x.", required=True)
    interval_type = fields.Selection([('minutes', 'Minutes'),
                                      ('hours', 'Hours'),
                                      ('days', 'Days'),
                                      ('weeks', 'Weeks'),
                                      ('months', 'Months')], string='Interval Unit', default='months', required=True)
    nextcall = fields.Datetime(string='Next Execution Date', required=True, default=fields.Datetime.now, help="Next planned execution date for this job.")
    lastcall = fields.Datetime(string='Last Execution Date', help="Previous time the cron ran successfully, provided to the job through the context on the `lastcall` key")
    priority = fields.Integer(default=5, aggregator=None, help='The priority of the job, as an integer: 0 means higher priority, 10 means lower priority.')
    failure_count = fields.Integer(default=0, help="The number of consecutive failures of this job. It is automatically reset on success.")
    first_failure_date = fields.Datetime(string='First Failure Date', help="The first time the cron failed. It is automatically reset on success.")

    _sql_constraints = [
        (
            'check_strictly_positive_interval',
            'CHECK(interval_number > 0)',
            'The interval number must be a strictly positive number.'
        ),
    ]

    @api.depends('ir_actions_server_id.name')
    def _compute_cron_name(self):
        for cron in self.with_context(lang='en_US'):
            cron.cron_name = cron.ir_actions_server_id.name