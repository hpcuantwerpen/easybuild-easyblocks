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
EasyBuild support for installing MCR, implemented as an easyblock

@author: Stijn De Weirdt (Ghent University)
@author: Dries Verdegem (Ghent University)
@author: Kenneth Hoste (Ghent University)
@author: Pieter De Baets (Ghent University)
@author: Jens Timmerman (Ghent University)
@author: Fotis Georgatos (Uni.Lu, NTUA)
@author: Balazs Hajgato (Vrije Universiteit Brussel)
"""
import glob
import os
import re
import shutil
import stat
from easybuild.tools import LooseVersion

from easybuild.easyblocks.generic.packedbinary import PackedBinary
from easybuild.framework.easyconfig import CUSTOM
from easybuild.tools.build_log import EasyBuildError
from easybuild.tools.filetools import adjust_permissions, read_file, write_file
from easybuild.tools.run import run_shell_cmd


class EB_MCR(PackedBinary):
    """Support for installing MCR."""

    def __init__(self, *args, **kwargs):
        """Add extra config options specific to MCR."""
        super().__init__(*args, **kwargs)
        self.comp_fam = None
        self.configfilename = "my_installer_input.txt"
        self.subdir = None

    @staticmethod
    def extra_options():
        """Custom easyconfig parameters for MCR."""
        extra_vars = {
            'java_options': ['-Xmx256m', "$_JAVA_OPTIONS value set for install and in module file.", CUSTOM],
        }
        return PackedBinary.extra_options(extra_vars)

    def configure_step(self):
        """Configure MCR installation: create license file."""

        configfile = os.path.join(self.builddir, self.configfilename)
        if LooseVersion(self.version) < LooseVersion('R2015a'):
            shutil.copyfile(os.path.join(self.cfg['start_dir'], 'installer_input.txt'), configfile)
            config = read_file(configfile)
            # compile regex first since re.sub doesn't accept re.M flag for multiline regex in Python 2.6
            regdest = re.compile(r"^# destinationFolder=.*", re.M)
            regagree = re.compile(r"^# agreeToLicense=.*", re.M)
            regmode = re.compile(r"^# mode=.*", re.M)

            config = regdest.sub("destinationFolder=%s" % self.installdir, config)
            config = regagree.sub("agreeToLicense=Yes", config)
            config = regmode.sub("mode=silent", config)
        elif LooseVersion(self.version) < LooseVersion('R2024a'):
            config = '\n'.join([
                "destinationFolder=%s" % self.installdir,
                "agreeToLicense=Yes",
                "mode=silent",
            ])
        else:
            config = '\n'.join([
                "destinationFolder=%s" % self.installdir,
                "agreeToLicense=yes",
            ])

        write_file(configfile, config)

        self.log.debug("configuration file written to %s:\n %s", configfile, config)

    def install_step(self):
        """MCR install procedure using 'install' command."""

        src = os.path.join(self.cfg['start_dir'], 'install')

        # make sure install script is executable
        adjust_permissions(src, stat.S_IXUSR)

        # make sure $DISPLAY is not defined, which may lead to (hard to trace) problems
        # this is a workaround for not being able to specify --nodisplay to the install scripts
        if 'DISPLAY' in os.environ:
            os.environ.pop('DISPLAY')

        if '_JAVA_OPTIONS' not in self.cfg['preinstallopts']:
            java_options = 'export _JAVA_OPTIONS="%s" && ' % self.cfg['java_options']
            self.cfg['preinstallopts'] = java_options + self.cfg['preinstallopts']

        configfile = "%s/%s" % (self.builddir, self.configfilename)
        cmd = "%s ./install -v -inputFile %s %s" % (self.cfg['preinstallopts'], configfile, self.cfg['installopts'])
        run_shell_cmd(cmd)

    def sanity_check_step(self):
        """Custom sanity check for MCR."""
        self.set_subdir()
        if not isinstance(self.subdir, str):
            raise EasyBuildError("Could not identify which subdirectory to pick: %s" % self.subdir)

        custom_paths = {
            'files': [],
            'dirs': [os.path.join(self.subdir, 'bin', 'glnxa64')],
        }

        if LooseVersion(self.version) >= LooseVersion('R2016b'):
            custom_paths['dirs'].append(os.path.join(self.subdir, 'cefclient', 'sys', 'os', 'glnxa64'))
        else:
            custom_paths['dirs'].extend([
                os.path.join(self.subdir, 'runtime', 'glnxa64'),
                os.path.join(self.subdir, 'sys', 'os', 'glnxa64'),
            ])
        super().sanity_check_step(custom_paths=custom_paths)

    def make_module_extra(self):
        """Extend PATH and set proper _JAVA_OPTIONS (e.g., -Xmx)."""
        txt = super().make_module_extra()

        self.set_subdir()
        # if no subdir was selected, set it to NOTFOUND
        # this is done to enable the use of --module-only without having an actual MCR installation
        if not isinstance(self.subdir, str):
            self.subdir = 'NOTFOUND'

        xapplresdir = os.path.join(self.installdir, self.subdir, 'X11', 'app-defaults')
        txt += self.module_generator.set_environment('XAPPLRESDIR', xapplresdir)
        for ldlibdir in ['runtime', 'bin', os.path.join('sys', 'os')]:
            libdir = os.path.join(self.subdir, ldlibdir, 'glnxa64')
            txt += self.module_generator.prepend_paths('LD_LIBRARY_PATH', libdir)

        txt += self.module_generator.set_environment('_JAVA_OPTIONS', self.cfg['java_options'])
        txt += self.module_generator.set_environment('MCRROOT', os.path.join(self.installdir, self.subdir))

        return txt

    def set_subdir(self):
        """Determine subdirectory in installation directory"""
        # no-op is self.subdir is already set
        if self.subdir is None:
            # determine subdirectory
            if LooseVersion(self.version) < LooseVersion('R2022b'):
                # (e.g. v84 (2014a, 2014b), v85 (2015a), ...)
                subdirs = glob.glob(os.path.join(self.installdir, 'v[0-9][0-9]*'))
            else:
                # (e.g. R2023a, R2023b, ...)
                subdirs = glob.glob(os.path.join(self.installdir, 'R[0-9][0-9][0-9][0-9]*'))
            if len(subdirs) == 1:
                self.subdir = os.path.basename(subdirs[0])
            else:
                self.subdir = subdirs
