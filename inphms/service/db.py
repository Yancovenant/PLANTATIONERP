# -*- coding: utf-8 -*-
import base64
import json
import logging
import os
import shutil
import subprocess
import tempfile
import zipfile
from contextlib import closing
from xml.etree import ElementTree as ET

import psycopg2
from psycopg2.extensions import quote_ident
from decorator import decorator
from pytz import country_timezones

import inphms
import inphms.release
import inphms.sql_db
import inphms.tools
from inphms import SUPERUSER_ID
# from inphms.exceptions import AccessDenied
from inphms.release import version_info
from inphms.sql_db import db_connect
from inphms.tools import SQL
# from inphms.tools.misc import exec_pg_environ, find_pg_tool

_logger = logging.getLogger(__name__)






