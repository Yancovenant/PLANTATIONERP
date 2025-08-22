# ruff: noqa: F401, PLC0415
# ignore import not at top of the file
import os
import time
from .evented import patch_evented

def set_timezone_utc():
    os.environ['TZ'] = 'UTC'  # Set the timezone
    if hasattr(time, 'tzset'):
        time.tzset()