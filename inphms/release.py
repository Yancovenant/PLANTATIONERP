# Coding - utf-8
# Part of Inphms, see License file for full copyright and licensing details.

RELEASE_LEVELS = [ALPHA, BETA, CANDIDATE, FINAL] = ['alpha', 'beta', 'candidate', 'final']
RELEASE_LEVELS_DISPLAY = {
    ALPHA: 'ALPHA',
    BETA: 'BETA',
    CANDIDATE: 'CANDIDATE',
    FINAL: '',
}

# version_info format: (MAJOR, MINOR, MICRO, RELEASE_LEVEL, SERIAL)
# inspired by Python's own sys.version_info, in order to be
# properly comparable using normal operators, for example:
#  (6,1,0,'beta',0) < (6,1,0,'candidate',1) < (6,1,0,'candidate',2)
#  (6,1,0,'candidate',2) < (6,1,0,'final',0) < (6,1,2,'final',0)
version_info = (0, 0, 1, BETA, 0, '')
version = '.'.join(str(s) for s in version_info[:2]) + RELEASE_LEVELS_DISPLAY[version_info[3]] + str(version_info[4] or '') + version_info[5]
series = serie = major_version = '.'.join(str(s) for s in version_info[:2])

product_name = 'Inphms'
description = 'Inphms server'
long_desc = '''Inphms is a complete Plantation ERP.'''
classifiers = """Development Status :: 4 - Beta
License :: OSI Approved :: GNU General Public License v3 (GPLv3)

Programing Language :: Python
"""
url = 'https://www.inphms.com'
author = 'Inphms'
author_email = 'info@inphms.com'
license = 'GPLv3'

nt_service_name = "inphms-server-" + series.replace('~','-')