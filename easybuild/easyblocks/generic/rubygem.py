##
# Copyright 2015-2025 Ghent University
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
EasyBuild support for Ruby Gems, implemented as an easyblock

@author: Robert Schmidt (Ottawa Hospital Research Institute)
@author: Kenneth Hoste (Ghent University)
"""
import os

import easybuild.tools.environment as env
from easybuild.framework.easyconfig import CUSTOM
from easybuild.framework.extensioneasyblock import ExtensionEasyBlock
from easybuild.tools.build_log import EasyBuildError
from easybuild.tools.filetools import copy_file
from easybuild.tools.modules import get_software_root
from easybuild.tools.run import run_shell_cmd


class RubyGem(ExtensionEasyBlock):
    """Builds and installs Ruby Gems."""

    @staticmethod
    def extra_options(extra_vars=None):
        """Extra easyconfig parameters specific to RubyGem easyblock."""
        extra_vars = ExtensionEasyBlock.extra_options(extra_vars)
        extra_vars.update({
            'gem_file': [None, "Path to gem file in unpacked sources", CUSTOM],
        })
        return extra_vars

    def __init__(self, *args, **kwargs):
        """RubyGem easyblock constructor."""
        super().__init__(*args, **kwargs)
        self.ext_src = None

    def install_extension(self):
        """Perform the actual Ruby gem build/install"""
        if not self.src:
            raise EasyBuildError("No source found for Ruby Gem %s, required for installation.", self.name)

        super().install_extension()

        self.ext_src = self.src
        self.log.debug("Installing Ruby gem %s version %s." % (self.name, self.version))
        self.install_step()

    def extract_step(self):
        """Skip extraction of .gem files, which are installed as downloaded"""
        if len(self.src) > 1:
            raise EasyBuildError("Don't know how to handle Ruby gems with multiple sources.")
        else:
            src = self.src[0]
            if src['path'].endswith('.gem'):
                copy_file(src['path'], self.builddir)
                self.ext_src = src['name']
                # set final path since it can't be determined from unpacked sources (used for guessing start_dir)
                src['finalpath'] = self.builddir
            else:
                # unpack zipped gems, use specified path to gem file
                super().extract_step()

    def configure_step(self):
        """No separate configuration for Ruby Gems."""
        pass

    def build_step(self):
        src = self.src[0]
        if self.cfg['gem_file']:
            self.ext_src = os.path.join(src['finalpath'], self.cfg['gem_file'])
            if not os.path.exists(self.ext_src):
                raise EasyBuildError("Gem file not found at %s", self.ext_src)
        else:
            gemfile = "%s.gem" % self.name
            gemfile_lower = "%s.gem" % self.name.lower()
            if os.path.exists(gemfile):
                self.ext_src = os.path.join(src['finalpath'], gemfile)
            elif os.path.exists(gemfile_lower):
                self.ext_src = os.path.join(src['finalpath'], gemfile_lower)
            else:
                gemspec = "%s.gemspec" % self.name
                gemspec_lower = "%s.gemspec" % self.name.lower()
                if os.path.exists(gemspec):
                    run_shell_cmd("gem build %s -o %s.gem" % (gemspec, self.name))
                    self.ext_src = "%s.gem" % self.name
                elif os.path.exists(gemspec_lower):
                    run_shell_cmd("gem build %s -o %s.gem" % (gemspec_lower, self.name.lower()))
                    self.ext_src = "%s.gem" % self.name.lower()
                else:
                    raise EasyBuildError("No gem_file specified and no"
                                         " %s.gemspec or %s.gemspec found." % (self.name, self.name.lower()))

    def test_step(self):
        """No separate (standard) test procedure for Ruby Gems."""
        pass

    def install_step(self):
        """Install Ruby Gems using gem package manager"""
        ruby_root = get_software_root('Ruby')
        if not ruby_root:
            raise EasyBuildError("Ruby module not loaded?")

        # this is the 'proper' way to specify a custom installation prefix: set $GEM_HOME
        if not self.is_extension or self.master.name != 'Ruby':
            env.setvar('GEM_HOME', self.installdir)

        cmd = ' '.join([
            self.cfg['preinstallopts'],
            'gem install',
            '--bindir ' + os.path.join(self.installdir, 'bin'),
            '--local ' + self.ext_src,
        ])
        run_shell_cmd(cmd)

    def make_module_extra(self):
        """Extend $GEM_PATH in module file."""
        txt = super().make_module_extra()
        # for stand-alone Ruby gem installs, $GEM_PATH needs to be updated
        if not self.is_extension or self.master.name != 'Ruby':
            txt += self.module_generator.prepend_paths('GEM_PATH', [''])
        return txt
