##
# Copyright 2009-2025 Ghent University
#
# This file is part of EasyBuild,
# originally created by the HPC team of Ghent University (http://ugent.be/hpc/en),
# with support of Ghent University (http://ugent.be/hpc),
# the Flemish Supercomputer Centre (VSC) (https://www.vscentrum.be),
# Flemish Research Foundation (FWO) (http://www.fwo.be/en)
# and the Department of Economy, Science and Innovation (EWI) (http://www.ewi-vlaanderen.be/en).
#
# https://github.com/easybuilders/easybuild
#
# EasyBuild is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation v2.
#
# EasyBuild is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with EasyBuild.  If not, see <http://www.gnu.org/licenses/>.
##
"""
EasyBuild support for installing RPMs, implemented as an easyblock.

@author: Stijn De Weirdt (Ghent University)
@author: Dries Verdegem (Ghent University)
@author: Kenneth Hoste (Ghent University)
@author: Pieter De Baets (Ghent University)
@author: Jens Timmerman (Ghent University)
@author: Toon Willems (Ghent University)
"""

import glob
import os
import re
import tempfile
from easybuild.tools import LooseVersion
from os.path import expanduser

import easybuild.tools.environment as env
from easybuild.base import fancylogger
from easybuild.easyblocks.generic.binary import Binary
from easybuild.framework.easyconfig import CUSTOM
from easybuild.tools.build_log import EasyBuildError
from easybuild.tools.filetools import change_dir, mkdir, symlink, which
from easybuild.tools.run import run_shell_cmd


_log = fancylogger.getLogger('easyblocks.generic.rpm')


def rebuild_rpm(rpm_path, targetdir):
    """Rebuild the RPM on the specified location, to make it relocatable."""
    # make sure that rpmrebuild command is available
    if not which('rpmrebuild'):
        raise EasyBuildError("Command 'rpmrebuild' is required but not available. " +
                             "Please add it as a dependency or install it with the OS package manager.")

    rpmmacros = os.path.join(expanduser('~'), '.rpmmacros')
    if os.path.exists(rpmmacros):
        raise EasyBuildError("rpmmacros file %s found which will override any other settings, so exiting.", rpmmacros)

    rpmrebuild_tmpdir = os.path.join(tempfile.gettempdir(), "rpmrebuild")
    env.setvar("RPMREBUILD_TMPDIR", rpmrebuild_tmpdir)

    try:
        if not os.path.exists(rpmrebuild_tmpdir):
            os.makedirs(rpmrebuild_tmpdir)
            _log.debug("Created RPMREBUILD_TMPDIR dir %s" % rpmrebuild_tmpdir)
        if not os.path.exists(targetdir):
            os.makedirs(targetdir)
            _log.debug("Created target directory for rebuilt RPMs %s" % targetdir)
    except OSError as err:
        raise EasyBuildError("Failed to create directories for rebuilding RPM: %s", err)

    _log.debug("Rebuilding %s in %s to make it relocatable" % (rpm_path, targetdir))
    cmd = ' '.join([
        "rpmrebuild -v",
        # replace whathever prefix is set with '/'
        r"""--change-spec-whole='sed -e "s/^Prefix:.*/Prefix: \//"'""",
        # comment out any specifications that involve relative file path (starting with '.') (??)
        r"""--change-spec-whole='sed -e "s/^\(.*:[ ]\+\..*\)/#ERROR \1/"'""",
        "--notest-install",
        "-p -d",
        targetdir,
        rpm_path,
    ])
    run_shell_cmd(cmd)


class Rpm(Binary):
    """
    Support for installing RPM files.

    - sources is a list of rpms
    - installation is with --nodeps (so the sources list has to be complete)
    """

    def __init__(self, *args, **kwargs):
        """Initialize class variables."""
        super().__init__(*args, **kwargs)

        self.rebuild_rpm = False

        # Add common PATH/LD_LIBRARY_PATH paths found in RPMs to module load environment
        self.module_load_environment.PATH = [os.path.join('usr', 'bin'), 'sbin', os.path.join('usr', 'sbin')]
        self.module_load_environment.LD_LIBRARY_PATH = [os.path.join('usr', 'lib'), os.path.join('usr', 'lib64')]
        self.module_load_environment.MANPATH = [os.path.join('usr', 'share', 'man')]

    @staticmethod
    def extra_options(extra_vars=None):
        """Extra easyconfig parameters specific to RPMs."""
        extra_vars = Binary.extra_options(extra_vars)
        extra_vars.update({
            'force': [False, "Use force", CUSTOM],
            'preinstall': [False, "Enable pre install", CUSTOM],
            'postinstall': [False, "Enable post install", CUSTOM],
            'makesymlinks': [[], "Create symlinks for listed paths", CUSTOM],  # supports glob
        })
        return extra_vars

    def extract_step(self):
        """
        Extract sources if requested, retain resulting list of RPMs as new list of sources.
        """
        super().extract_step()

        if self.cfg.get('extract_sources', False):
            self.src = []
            for src_rpm in sorted(glob.glob(os.path.join(self.builddir, '*.rpm'))):
                self.src.append({
                    'name': os.path.basename(src_rpm),
                    'path': src_rpm,
                    'finalpath': self.builddir,
                })
            self.log.info("New list of sources after unpacking: %s", self.src)

    def configure_step(self):
        """Custom configuration procedure for RPMs: rebuild RPMs for relocation if required."""

        # make sure that rpm is available
        if not which('rpm'):
            raise EasyBuildError("Command 'rpm' is required but not available.")

        # determine whether RPMs need to be rebuilt to make relocation work
        cmd = "rpm --version"
        res = run_shell_cmd(cmd)

        rpmver_re = re.compile(r"^RPM\s+version\s+(?P<version>[0-9.]+).*")
        res = rpmver_re.match(res.output)
        self.log.debug("RPM version found: %s" % res.group())

        if res:
            ver = res.groupdict()['version']

            # rebuilding is required on SL6, which implies rpm v4.8 (works fine without rebuilding on SL5)
            if LooseVersion(ver) >= LooseVersion('4.8.0'):
                self.rebuild_rpm = True
                self.log.debug("Enabling rebuild of RPMs to make relocation work...")
        else:
            raise EasyBuildError("Checking RPM version failed, so just carrying on with the default behaviour...")

        if self.rebuild_rpm:
            self.rebuild_rpms()

    # when installing RPMs under a non-default path for e.g. SL6,
    # --relocate doesn't seem to work (error: Unable to change root directory: Operation not permitted)
    def rebuild_rpms(self):
        """Rebuild RPMs to make relocation work."""
        for rpm in self.src:
            rebuild_rpm(rpm['path'], targetdir=self.builddir)

        self.oldsrc = self.src
        self.src = []
        for path in glob.glob(os.path.join(self.builddir, '*', '*.rpm')):
            self.src.append({
                'name': os.path.basename(path),
                'path': path,
            })
        self.log.debug("oldsrc: %s, src: %s" % (self.oldsrc, self.src))

    def install_step(self):
        """Custom installation procedure for RPMs into a custom prefix."""

        change_dir(self.installdir)
        mkdir('rpm')

        cmd = "rpm --initdb --dbpath /rpm --root %s" % self.installdir

        run_shell_cmd(cmd)

        force = ''
        if self.cfg['force']:
            force = '--force'

        postinstall = '--nopost'
        if self.cfg['postinstall']:
            postinstall = ''
        preinstall = '--nopre'
        if self.cfg['preinstall']:
            preinstall = ''

        if self.rebuild_rpm:
            cmd_tpl = "%(preinstallopts)s rpm -i --dbpath %(inst)s/rpm %(force)s --relocate /=%(inst)s " \
                      "%(pre)s %(post)s --nodeps --ignorearch %(rpm)s %(installopts)s"
        else:
            cmd_tpl = "%(preinstallopts)s rpm -i --dbpath /rpm %(force)s --root %(inst)s --relocate /=%(inst)s " \
                      "%(pre)s %(post)s --nodeps %(rpm)s %(installopts)s"

        # exception for user root:
        # --relocate is not necessary -> --root will relocate more than enough
        # cmd_tpl = "rpm -i --dbpath /rpm %(force)s --root %(inst)s %(pre)s %(post)s --nodeps %(rpm)s"

        for rpm in self.src:
            cmd = cmd_tpl % {
                'preinstallopts': self.cfg['preinstallopts'],
                'inst': self.installdir,
                'rpm': rpm['path'],
                'force': force,
                'pre': preinstall,
                'post': postinstall,
                'installopts': self.cfg['installopts'],
            }
            run_shell_cmd(cmd)

        for path in self.cfg['makesymlinks']:
            # allow globs, always use first hit.
            # also verify links existince
            realdirs = glob.glob(path)
            if realdirs:
                if len(realdirs) > 1:
                    self.log.debug("More then one match found for symlink glob %s, using first (all: %s)",
                                   path, realdirs)
                symlink(realdirs[0], os.path.join(self.installdir, os.path.basename(path)))
            else:
                self.log.debug("No match found for symlink glob %s." % path)
