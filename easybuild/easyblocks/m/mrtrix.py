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
EasyBuild support for building and installing MRtrix, implemented as an easyblock
"""
import glob
import os
from easybuild.tools import LooseVersion

import easybuild.tools.environment as env
from easybuild.framework.easyblock import EasyBlock
from easybuild.tools.filetools import copy, symlink
from easybuild.tools.run import run_shell_cmd
from easybuild.tools.systemtools import get_shared_lib_ext


class EB_MRtrix(EasyBlock):
    """Support for building/installing MRtrix."""

    def __init__(self, *args, **kwargs):
        """Initialize easyblock, enable build-in-installdir based on version."""
        super().__init__(*args, **kwargs)

        if LooseVersion(self.version) >= LooseVersion('0.3') and LooseVersion(self.version) < LooseVersion('0.3.14'):
            self.build_in_installdir = True
            self.log.debug("Enabled build-in-installdir for version %s", self.version)

        self.module_load_environment.PATH.append('scripts')

        if LooseVersion(self.version) >= LooseVersion('3.0'):
            self.module_load_environment.PYTHONPATH = ['lib']

    def extract_step(self):
        """Extract MRtrix sources."""
        # strip off 'mrtrix*' part to avoid having everything in a 'mrtrix*' subdirectory
        if LooseVersion(self.version) >= LooseVersion('0.3'):
            self.cfg.update('unpack_options', '--strip-components=1')

        super().extract_step()

    def configure_step(self):
        """No configuration step for MRtrix."""
        if LooseVersion(self.version) >= LooseVersion('0.3'):
            if LooseVersion(self.version) < LooseVersion('0.3.13'):
                env.setvar('LD', "%s LDFLAGS OBJECTS -o EXECUTABLE" % os.getenv('CXX'))
                env.setvar('LDLIB', "%s -shared LDLIB_FLAGS OBJECTS -o LIB" % os.getenv('CXX'))

            env.setvar('QMAKE_CXX', os.getenv('CXX'))
            cmd = "python configure -verbose"

            run_shell_cmd(cmd)

    def build_step(self):
        """Custom build procedure for MRtrix."""
        env.setvar('NUMBER_OF_PROCESSORS', str(self.cfg.parallel))

        cmd = "python build -verbose"
        run_shell_cmd(cmd)

    def install_step(self):
        """Custom install procedure for MRtrix."""
        if LooseVersion(self.version) < LooseVersion('0.3'):
            cmd = "python build -verbose install=%s linkto=" % self.installdir
            run_shell_cmd(cmd)

        elif LooseVersion(self.version) >= LooseVersion('3.0'):
            copy(os.path.join(self.builddir, 'bin'), self.installdir)
            copy(os.path.join(self.builddir, 'lib'), self.installdir)

        elif LooseVersion(self.version) >= LooseVersion('0.3.14'):
            copy(glob.glob(os.path.join(self.builddir, 'release', '*')), self.installdir)
            copy(os.path.join(self.builddir, 'scripts'), self.installdir)
            # some scripts expect 'release/bin' to be there, so we put a symlink in place
            symlink(self.installdir, os.path.join(self.installdir, 'release'))

    def sanity_check_step(self):
        """Custom sanity check for MRtrix."""
        shlib_ext = get_shared_lib_ext()

        if LooseVersion(self.version) >= LooseVersion('0.3'):
            libso = 'libmrtrix.%s' % shlib_ext
        else:
            libso = 'libmrtrix-%s.%s' % ('_'.join(self.version.split('.')), shlib_ext)
        custom_paths = {
            'files': [os.path.join('lib', libso)],
            'dirs': ['bin'],
        }

        custom_commands = []
        if LooseVersion(self.version) >= LooseVersion('3.0'):
            custom_commands.append("python -c 'import mrtrix3'")

        super().sanity_check_step(custom_paths=custom_paths, custom_commands=custom_commands)
