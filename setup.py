#!/usr/bin/env python
# -*- coding: utf-8 -*-

from setuptools import setup, find_packages
from os.path import join, dirname

exec(open(join(dirname(__file__), 'inphms', 'release.py'), 'rb').read())
lib_name = 'inphms'

setup(
    name='inphms',
    version=version,
    description=description,
    long_description=long_desc,
    url=url,
    author=author,
    author_email=author_email,
    classifiers=[c for c in classifiers.split('\n') if c],
    license=license,
    scripts=['setup/inphms'],
    packages=find_packages(),
    package_dir={'%s' % lib_name: 'inphms'},
    include_package_data=True,
    install_requires=[
        'werkzeug',
        'jinja2',
        'psycopg2 >= 2.2',
        'passlib',
        'decorator',
        'requests',
        'babel >= 1.0',
    ],
    python_requires='>=3.10',
    extras_require={
        'ldap': ['python-ldap']
    },
    tests_require=[
        'freezegun',
    ],
)