# #
# Copyright 2013-2025 Ghent University
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
# #
"""
EasyBuild support for installing EasyBuild, implemented as an easyblock

@author: Kenneth Hoste (UGent)
"""
import copy
import os
import re
import sys
from collections import OrderedDict

from easybuild.easyblocks.generic.pythonpackage import PythonPackage
from easybuild.tools import LooseVersion
from easybuild.tools.build_log import EasyBuildError
from easybuild.tools.filetools import apply_regex_substitutions, change_dir, read_file, write_file
from easybuild.tools.modules import get_software_root_env_var_name
from easybuild.tools.utilities import flatten


EGGINFO = """Metadata-Version: 2.1
Name: easybuild
Version: %s
Summary: %s
Platform: UNKNOWN
"""


# note: we can't use EB_EasyBuild as easyblock name, as that would require an easyblock named 'easybuild.py',
#       which would screw up namespacing and create all kinds of problems (e.g. easyblocks not being found anymore)
class EB_EasyBuildMeta(PythonPackage):
    """Support for install EasyBuild."""

    def __init__(self, *args, **kwargs):
        """Initialize custom class variables."""
        super().__init__(*args, **kwargs)

        self.real_initial_environ = copy.deepcopy(self.initial_environ)

        self.easybuild_pkgs = ['easybuild-framework', 'easybuild-easyblocks', 'easybuild-easyconfigs']

        # check whether 'easybuild' package is part of the sources
        self.with_easybuild_pkg = False
        for source in self.cfg['sources']:
            if isinstance(source, dict):
                pkg_name = source.get('filename')
                if pkg_name is None:
                    raise EasyBuildError(f"Unknown filename for source: {source}")
            elif isinstance(source, str):
                pkg_name = source
            else:
                raise EasyBuildError(f"Unknown type of source specification: {source}")

            if pkg_name == f'easybuild-{self.version}':
                self.with_easybuild_pkg = True
                break

        if LooseVersion(self.version) >= LooseVersion('2.0') and LooseVersion(self.version) <= LooseVersion('3.999'):
            # deliberately include vsc-install & vsc-base twice;
            # first time to ensure the specified vsc-install/vsc-base package is available when framework gets installed
            self.easybuild_pkgs.insert(0, 'vsc-base')
            self.easybuild_pkgs.insert(0, 'vsc-install')
            # second time as last package to be installed, to ensure that the vsc-base version listed
            # in easy-install.pth is the one specified;
            # when installing the easybuild-* packages, the vsc-base version in easy-install.pth may be 'bumped'
            # if a newer vsc-base version is found somewhere (e.g. provided by the OS)
            self.easybuild_pkgs.extend(['vsc-base', 'vsc-install'])
            # consider setuptools first, in case it is listed as a sources
            self.easybuild_pkgs.insert(0, 'setuptools')
        elif LooseVersion(self.version) >= LooseVersion('5.0') and self.with_easybuild_pkg:
            # use easybuild-base for easybuild to avoid matching all easybuild-* directories during install
            self.easybuild_pkgs.append('easybuild-base')

    # Override this function since we want to respect the user choice for the python installation to use
    # (which can be influenced by EB_PYTHON and EB_INSTALLPYTHON)
    def prepare_python(self):
        """Python-specific preparations."""

        self.python_cmd = sys.executable
        # set Python lib directories
        self.set_pylibdirs()

        self.log.info("Python command being used: %s", self.python_cmd)

    def check_readiness_step(self):
        """Make sure EasyBuild can be installed with a loaded EasyBuild module."""
        env_var_name = get_software_root_env_var_name(self.name)
        if env_var_name in os.environ:
            os.environ.pop(env_var_name)
            self.log.debug("$%s is unset so EasyBuild can be installed with a loaded EasyBuild module" % env_var_name)
        else:
            self.log.debug("Not unsetting $%s since it's not set" % env_var_name)

        super().check_readiness_step()

    def build_step(self):
        """No building for EasyBuild packages."""
        pass

    def fix_easyconfigs_setup_py_setuptools61(self):
        """
        Patch setup.py of easybuild-easyconfigs package if needed to make sure that installation works
        for recent setuptools versions (>= 61.0).
        """
        # cfr. https://github.com/easybuilders/easybuild-easyconfigs/pull/15206
        cwd = os.getcwd()
        regex = re.compile(r'packages=\[\]')
        setup_py_txt = read_file('setup.py')
        if regex.search(setup_py_txt) is None:
            self.log.info("setup.py at %s needs to be fixed to install with setuptools >= 61.0", cwd)
            apply_regex_substitutions('setup.py', [(r'^setup\(', 'setup(packages=[],')])
        else:
            self.log.info("setup.py at %s does not need to be fixed to install with setuptools >= 61.0", cwd)

    def install_step(self):
        """Install EasyBuild packages one by one."""
        try:
            subdirs = os.listdir(self.builddir)
            for pkg in self.easybuild_pkgs:
                # also consider "normalized" package name, with dashes ('-') replaced by underscores ('_'),
                # which is being enforced by recent versions of setuptools (>= 69.0.3?)
                pkg_norm = pkg.replace('-', '_')
                seldirs = [x for x in subdirs if x.startswith(pkg) or x.startswith(pkg_norm)]
                if len(seldirs) != 1:
                    # setuptools is optional since it may be available in the OS;
                    # vsc-install and vsc-base sources are optional,
                    # they can be pulled in from PyPi when installing easybuild-framework too
                    if pkg not in ['setuptools', 'vsc-base', 'vsc-install']:
                        raise EasyBuildError("Failed to find required EasyBuild package %s (subdirs: %s, seldirs: %s)",
                                             pkg, subdirs, seldirs)

                else:
                    self.log.info("Installing package %s", pkg)
                    change_dir(os.path.join(self.builddir, seldirs[0]))

                    if pkg == 'easybuild-easyconfigs':
                        self.fix_easyconfigs_setup_py_setuptools61()

                    super().install_step()

            if not self.with_easybuild_pkg:
                egginfo = os.path.join(self.installdir, self.pylibdir, f'easybuild-{self.version}.egg-info')
                write_file(egginfo, EGGINFO % (self.version, ''.join(self.cfg['description'].splitlines())))

        except OSError as err:
            raise EasyBuildError("Failed to install EasyBuild packages: %s", err)

    def post_processing_step(self):
        """Remove setuptools.pth file that hard includes a system-wide (site-packages) path, if it is there."""

        super().post_processing_step()

        setuptools_pth = os.path.join(self.installdir, self.pylibdir, 'setuptools.pth')
        if os.path.exists(setuptools_pth):
            setuptools_pth_txt = read_file(setuptools_pth)
            # any line that starts with '/' is a sign of trouble
            sys_path_regex = re.compile('^/', re.M)
            if sys_path_regex.search(setuptools_pth_txt):
                self.log.warning("Found %s, and includes one or more absolute system paths. Removing it.",
                                 setuptools_pth)
                try:
                    os.remove(setuptools_pth)
                except OSError as err:
                    raise EasyBuildError("Failed to remove %s: %s", setuptools_pth, err)

    def sanity_check_step(self):
        """Custom sanity check for EasyBuild."""

        # check whether easy-install.pth contains correct entries
        easy_install_pth = os.path.join(self.installdir, self.pylibdir, 'easy-install.pth')
        if os.path.exists(easy_install_pth):
            easy_install_pth_txt = read_file(easy_install_pth)

            ignore_pkgs = ['setuptools', 'vsc-install']
            if LooseVersion(self.version) > LooseVersion('3.999'):
                ignore_pkgs.append('vsc-base')

            for pkg in [p for p in self.easybuild_pkgs if p not in ignore_pkgs]:
                if pkg == 'vsc-base':
                    # don't include strict version check for vsc-base
                    pkg_regex = re.compile(r"^\./%s" % pkg.replace('-', '_'), re.M)
                else:
                    major_minor_version = '.'.join(self.version.split('.')[:2])
                    pkg_regex = re.compile(r"^\./%s-%s" % (pkg.replace('-', '_'), major_minor_version), re.M)

                if not pkg_regex.search(easy_install_pth_txt):
                    raise EasyBuildError("Failed to find pattern '%s' in %s: %s",
                                         pkg_regex.pattern, easy_install_pth, easy_install_pth_txt)

        # list of dirs to check, by package
        # boolean indicates whether dir is expected to reside in Python lib/pythonX/site-packages dir
        subdirs_by_pkg = {
            'easybuild-framework': [('easybuild/framework', True), ('easybuild/tools', True)],
            'easybuild-easyblocks': [('easybuild/easyblocks', True)],
            'easybuild-easyconfigs': [('easybuild/easyconfigs', False)],
        }
        if LooseVersion(self.version) >= LooseVersion('2.0') and LooseVersion(self.version) < LooseVersion('3.999'):
            subdirs_by_pkg.update({
                'vsc-base': [('vsc/utils', True)],
            })

        # final list of directories to check, by setup tool
        # order matters, e.g. setuptools before distutils
        eb_dirs = OrderedDict()
        eb_dirs['setuptools'] = []
        eb_dirs['distutils.core'] = flatten([x for x in subdirs_by_pkg.values()])

        # determine setup tool (setuptools or distutils)
        setup_tool = None
        for tool in eb_dirs.keys():
            self.log.debug("Trying %s.." % tool)
            try:
                exec("from %s import setup" % tool)
                setup_tool = tool
                break
            except ImportError:
                pass
        self.log.debug('setup_tool: %s' % setup_tool)

        # for a setuptools installation, we need to figure out the egg dirs,
        # since we don't know the individual package versions
        if setup_tool == 'setuptools':
            try:
                installed_dirs = os.listdir(os.path.join(self.installdir, self.pylibdir))
                for (pkg, subdirs) in subdirs_by_pkg.items():
                    sel_dirs = [x for x in installed_dirs if x.startswith(pkg.replace('-', '_'))]
                    if not len(sel_dirs) == 1:
                        raise EasyBuildError("Failed to isolate installed egg dir for %s", pkg)

                    for (subdir, _) in subdirs:
                        # eggs always go in Python lib/pythonX/site-packages dir with setuptools
                        eb_dirs['setuptools'].append((os.path.join(sel_dirs[0], subdir), True))
            except OSError as err:
                raise EasyBuildError("Failed to determine sanity check dir paths: %s", err)

        # set of sanity check paths to check for EasyBuild
        custom_paths = {
            'files': ['bin/eb'],
            'dirs': [self.pylibdir] + [[x, os.path.join(self.pylibdir, x)][y] for (x, y) in eb_dirs[setup_tool]],
        }

        # make sure we don't trip over deprecated behavior in old EasyBuild versions
        eb_cmd = 'eb'
        if LooseVersion(self.version) <= LooseVersion('1.16.0'):
            eb_cmd = 'EASYBUILD_DEPRECATED=1.0 eb'

        # set of sanity check commands to run for EasyBuild
        custom_commands = [
            # this may spit out a wrong version, but that should be safe to ignore
            # occurs when the EasyBuild being used is newer than the EasyBuild being installed
            (eb_cmd, '--version'),
            (eb_cmd, '-a'),
            (eb_cmd, '-e ConfigureMake -a'),
        ]

        # (temporary) cleanse copy of initial environment to avoid conflict with (potentially) loaded EasyBuild module
        for env_var in ['_LMFILES_', 'LOADEDMODULES', 'MODULES_LMCONFLICT', '__MODULES_LMCONFLICT']:
            if env_var in self.initial_environ:
                self.initial_environ.pop(env_var)
                os.environ.pop(env_var)
                self.log.debug("Unset $%s in current env and copy of original env to make sanity check work" % env_var)

        # unset all $EASYBUILD_* environment variables when running sanity check commands,
        # to prevent failing sanity check for old EasyBuild versions when configuration options are defined
        # via $EASYBUILD_* environment variables
        for key in [k for k in self.initial_environ if k.startswith('EASYBUILD_')]:
            val = self.initial_environ.pop(key)
            self.log.info("$%s found in environment, unset for running sanity check (was: %s)", key, val)

        super().sanity_check_step(custom_paths=custom_paths, custom_commands=custom_commands)

    def make_module_extra(self):
        """
        Set $EB_INSTALLPYTHON to ensure that this EasyBuild installation uses the same Python executable it was
        installed with (which can still be overridden by the user with $EB_PYTHON).
        """
        txt = super().make_module_extra()
        txt += self.module_generator.set_environment('EB_INSTALLPYTHON', self.python_cmd)
        return txt

    def make_module_step(self, fake=False):
        """Create module file, before copy of original environment that was tampered with is restored."""
        modpath = super().make_module_step(fake=fake)

        if not fake:
            # restore copy of original environment
            self.initial_environ = copy.deepcopy(self.real_initial_environ)
            self.log.debug("Restored copy of original environment")

        return modpath
