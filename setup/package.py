#!/usr/bin/env python3

# Part of Inphms, see License file for full copyright and licensing details.

import argparse
import logging
import os
# import pexpect
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import traceback

from pathlib import Path
from xmlrpc import client as xmlrpclib

from glob import glob

#----------------------------------------------------------
# Utils
#----------------------------------------------------------

ROOTDIR = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
TSTAMP = time.strftime("%Y%m%d", time.gmtime())
TSEC = time.strftime("%H%M%S", time.gmtime())
# Get some variables from release.py
version = ...
version_info = ...
nt_service_name = ...
exec(open(os.path.join(ROOTDIR, 'inphms', 'release.py'), 'rb').read())
VERSION = version.split('-')[0].replace('saas~', '')
GPGPASSPHRASE = os.getenv('GPGPASSPHRASE')
GPGID = os.getenv('GPGID')
DOCKERVERSION = VERSION.replace('+', '')
INSTALL_TIMEOUT = 600

if os.name == 'nt':
    DOCKERUSER = """
    RUN mkdir /var/lib/inphms && \
        groupadd -g 1000 inphms && \
        useradd -u 1000 -g inphms inphms -d /var/lib/inphms && \
        mkdir /data && \
        chown inphms:inphms /var/lib/inphms /data
    USER inphms
    """
else:
    DOCKERUSER = """
    RUN mkdir /var/lib/inphms && \
        groupadd -g %(group_id)s inphms && \
        useradd -u %(user_id)s -g inphms inphms -d /var/lib/inphms && \
        mkdir /data && \
        chown inphms:inphms /var/lib/inphms /data
    USER inphms
    """ % {'group_id': os.getgid(), 'user_id': os.getuid()}

def run_cmd(cmd, chdir=None, timeout=None):
    logging.info("Running command: '%s'", ' '.join(cmd))
    return subprocess.run(cmd, cwd=chdir, timeout=timeout)

def publish(args, pub_type, extensions):
    """Publish builded package (move builded files and generate a symlink to the latests)
    :args: parsed program args
    :pub_type: one of [deb, rpm, src, exe]
    :extensions: list of extensions to publish
    :returns: published files
    """
    def _publish(release):
        build_path = os.path.join(args.build_dir, release)
        filename = release.split(os.path.sep)[-1]
        release_dir = os.path.join(args.pub, pub_type)
        release_path = os.path.join(release_dir, filename)
        os.renames(build_path, release_path)

        # Latest/symlink handler
        release_abspath = os.path.abspath(release_path)
        latest_abspath = release_abspath.replace(TSTAMP, 'latest')
        if os.name == 'nt':
            if os.path.exists(latest_abspath):
                if os.path.islink(latest_abspath):
                    os.unlink(latest_abspath)
                else:
                    os.remove(latest_abspath)
            shutil.copy2(release_abspath, latest_abspath)
        else:
            if os.path.islink(latest_abspath):
                os.unlink(latest_abspath)
            os.symlink(release_abspath, latest_abspath)

        return release_path

    published = []
    for extension in extensions:
        release = glob("%s/inphms_*.%s" % (args.build_dir, extension))
        if release:
            published.append(_publish(release[0]))
    return published

def _prepare_build_dir(args, win32=False, move_addons=True):
    """Copy files to the build directory"""
    logging.info('Preparing build dir "%s"', args.build_dir)

    if os.name == 'nt':
        cmd = ['robocopy', args.inphms_dir, args.build_dir, '/E', '/XD', '.git', '__pycache__', '/XF', '*.pyc', '*.pyo']
        if not win32:
            cmd.extend(['/XD', 'setup\\win32'])
        result = run_cmd(cmd)
        # robocopy returns 0-7 for success, 8+ for errors
        if result.returncode > 7:
            logging.error("Robocopy failed with return code: %d", result.returncode)
            raise subprocess.CalledProcessError(result.returncode, cmd)
    else:
        cmd = ['rsync', '-a', '--delete', '--exclude', '.git', '--exclude', '*.pyc', '--exclude', '*.pyo']
        if win32 is False:
            cmd += ['--exclude', 'setup/win32']
        run_cmd(cmd + ['%s/' % args.inphms_dir, args.build_dir])

    if not move_addons:
        return
    for addon_path in glob(os.path.join(args.build_dir, 'addons/*')):
        if args.blacklist is None or os.path.basename(addon_path) not in args.blacklist:
            try:
                shutil.move(addon_path, os.path.join(args.build_dir, 'inphms/addons'))
            except shutil.Error as e:
                logging.warning("Warning '%s' while moving addon '%s", e, addon_path)
                if addon_path.startswith(args.build_dir) and os.path.isdir(addon_path):
                    logging.info("Removing '{}'".format(addon_path))
                    try:
                        shutil.rmtree(addon_path)
                    except shutil.Error as rm_error:
                        logging.warning("Cannot remove '{}': {}".format(addon_path, rm_error))

class Docker():
    """Base Docker class. Must be inherited by specific Docker builder class"""
    arch = None

    def __init__(self, args):
        """
        :param args: argparse parsed arguments
        """
        self.args = args
        self.tag = 'inphms-%s-%s-nightly-tests' % (DOCKERVERSION.lower(), self.arch)
        self.container_name = None
        self.exposed_port = None
        docker_templates = {
            'tgz': os.path.join(args.build_dir, 'setup/package.dfsrc'),
            'deb': os.path.join(args.build_dir, 'setup/package.dfdebian'),
            'rpm': os.path.join(args.build_dir, 'setup/package.dffedora'),
            'win': os.path.join(args.build_dir, 'setup/package.dfwine'),
        }
        self.docker_template = Path(docker_templates[self.arch]).read_text(encoding='utf-8').replace('USER inphms', DOCKERUSER)
        self.test_log_file = '/data/src/test-%s.log' % self.arch
        self.docker_dir = Path(self.args.build_dir) / 'docker'
        if not self.docker_dir.exists():
            self.docker_dir.mkdir()
        self.build_image()
    
    def build_image(self):
        """Build the dockerimage by copying Dockerfile into build_dir/docker"""
        docker_file = self.docker_dir / 'Dockerfile'
        docker_file.write_text(self.docker_template)
        shutil.copy(os.path.join(self.args.build_dir, 'requirements.txt'), self.docker_dir)
        run_cmd(["docker", "build", "--rm=True", "-t", self.tag, "."], chdir=self.docker_dir, timeout=1200).check_returncode()
        shutil.rmtree(self.docker_dir)

class DockerWine(Docker):
    """Docker class to build Windows package"""
    arch = 'win'

    def build(self):
        logging.info('Start building windows package')
        winver = "%s.%s" % (VERSION.replace('~', '_').replace('+', ''), TSTAMP)
        container_python = '/var/lib/inphms/.wine/drive_c/inphmsbuild/WinPy64/python-3.12.3.amd64/python.exe'
        nsis_args = f'/DVERSION={winver} /DMAJOR_VERSION={version_info[0]} /DMINOR_VERSION={version_info[1]} /DSERVICENAME={nt_service_name} /DPYTHONVERSION=3.12.3'
        cmds = [
            rf'wine {container_python} -m pip install --upgrade pip',
            rf'cat /data/src/requirements*.txt  | while read PACKAGE; do wine {container_python} -m pip install "${{PACKAGE%%#*}}" ; done',
            rf'wine "c:\nsis-3.11\makensis.exe" {nsis_args} "c:\inphmsbuild\server\setup\win32\setup.nsi"',
            rf'wine {container_python} -m pip list'
        ]
        self.run(' && '.join(cmds), self.args.build_dir, 'inphms-win-build-%s' % TSTAMP)
        logging.info('Finished building Windows package')

def parse_args():
    ap = argparse.ArgumentParser()
    build_dir = "%s-%s-%s" % (ROOTDIR, TSEC, TSTAMP)
    log_levels = {"debug": logging.DEBUG, "info": logging.INFO, "warning": logging.WARN, "error": logging.ERROR, "critical": logging.CRITICAL}

    ap.add_argument("-b", "--build-dir", default=build_dir, help="build directory (%(default)s)", metavar="DIR")
    ap.add_argument("-p", "--pub", default=None, help="pub directory %(default)s", metavar="DIR")
    ap.add_argument("--logging", action="store", choices=list(log_levels.keys()), default="info", help="Logging level")
    ap.add_argument("--build-deb", action="store_true")
    ap.add_argument("--build-rpm", action="store_true")
    ap.add_argument("--build-tgz", action="store_true")
    ap.add_argument("--build-win", action="store_true")

    ap.add_argument("-t", "--test", action="store_true", default=False, help="Test built packages")
    ap.add_argument("-s", "--sign", action="store_true", default=False, help="Sign Debian package / generate Rpm repo")
    ap.add_argument("--no-remove", action="store_true", help="don't remove build dir")
    ap.add_argument("--blacklist", nargs="*", help="Modules to blacklist in package")

    parsed_args = ap.parse_args()
    logging.basicConfig(format='%(asctime)s %(levelname)s: %(message)s', datefmt='%Y-%m-%d %I:%M:%S', level=log_levels[parsed_args.logging])
    parsed_args.inphms_dir = ROOTDIR
    return parsed_args

def main(args):
    try:
        if args.build_tgz:
            _prepare_build_dir(args)
            docker_tgz = DockerTgz(args)
            docker_tgz.build()
            try:
                docker_tgz.start_test()
                published_files = publish(args, 'tgz', ['tar.gz', 'zip'])
            except Exception as e:
                logging.error("Won't publish the tgz release.\n Exception: %s" % str(e))
        if args.build_rpm:
            _prepare_build_dir(args)
            docker_rpm = DockerRpm(args)
            docker_rpm.build()
            try:
                docker_rpm.start_test()
                published_files = publish(args, 'rpm', ['rpm'])
                if args.sign:
                    logging.info('Signing rpm package')
                    rpm_sign(args, published_files[0])
                    logging.info('Generate rpm repo')
                    docker_rpm.gen_rpm_repo(args, published_files[0])
            except Exception as e:
                logging.error("Won't publish the rpm release.\n Exception: %s" % str(e))
        if args.build_deb:
            _prepare_build_dir(args, move_addons=False)
            docker_deb = DockerDeb(args)
            docker_deb.build()
            try:
                docker_deb.start_test()
                published_files = publish(args, 'deb', ['deb', 'dsc', 'changes', 'tar.xz'])
                gen_deb_package(args, published_files)
            except Exception as e:
                logging.error("Won't publish the deb release.\n Exception: %s" % str(e))
        if args.build_win:
            _prepare_build_dir(args, win32=True)
            docker_wine = DockerWine(args)
            docker_wine.build()
            try:
                published_files = publish(args, 'windows', ['exe'])
            except Exception as e:
                logging.error("Won't publish the exe release.\n Exception: %s" % str(e))
    except Exception as e:
        logging.error('Something bad happened ! : {}'.format(e))
        traceback.print_exc()
    finally:
        if args.no_remove:
            logging.info('Build dir "{}" not removed'.format(args.build_dir))
        else:
            if os.path.exists(args.build_dir):
                shutil.rmtree(args.build_dir)
                logging.info('Build dir %s removed' % args.build_dir)

if __name__ == '__main__':
    args = parse_args()
    if os.path.exists(args.build_dir):
        logging.error('Build dir "%s" already exists.', args.build_dir)
        sys.exit(1)
    main(args)