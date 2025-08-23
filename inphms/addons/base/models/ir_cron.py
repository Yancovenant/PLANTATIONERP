# Part of Inphms. See LICENSE file for full copyright and licensing details.

import inphms
from inphms import models


class ir_cron(models.Model):
    """ Model describing cron jobs (also called actions or tasks).
    """

    # TODO: perhaps in the future we could consider a flag on ir.cron jobs
    # that would cause database wake-up even if the database has not been
    # loaded yet or was already unloaded (e.g. 'force_db_wakeup' or something)
    # See also inphms.cron

    _name = 'ir.cron'