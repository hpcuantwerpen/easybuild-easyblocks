# #
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
# #
"""
Generic EasyBuild support for installing Intel tools, implemented as an easyblock

@author: Stijn De Weirdt (Ghent University)
@author: Dries Verdegem (Ghent University)
@author: Kenneth Hoste (Ghent University)
@author: Pieter De Baets (Ghent University)
@author: Jens Timmerman (Ghent University)
@author: Ward Poelmans (Ghent University)
@author: Lumir Jasiok (IT4Innovations)
@author: Damian Alvarez (Forschungszentrum Juelich GmbH)
"""

import os
import re
import shutil
import stat
import tempfile
from easybuild.tools import LooseVersion

import easybuild.tools.environment as env
from easybuild.framework.easyblock import EasyBlock
from easybuild.framework.easyconfig import CUSTOM
from easybuild.framework.easyconfig.types import ensure_iterable_license_specs
from easybuild.tools.build_log import EasyBuildError
from easybuild.tools.filetools import adjust_permissions, find_flexlm_license
from easybuild.tools.filetools import read_file, remove_file, write_file
from easybuild.tools.modules import MODULE_LOAD_ENV_HEADERS
from easybuild.tools.run import run_shell_cmd


# different supported activation types (cfr. Intel documentation)
ACTIVATION_EXIST_LIC = 'exist_lic'  # use a license which exists on the system
ACTIVATION_LIC_FILE = 'license_file'  # use a license file
ACTIVATION_LIC_SERVER = 'license_server'  # use a license server
ACTIVATION_SERIAL = 'serial_number'  # use a serial number
ACTIVATION_TRIAL = 'trial_lic'  # use trial activation
ACTIVATION_TYPES = [
    ACTIVATION_EXIST_LIC,
    ACTIVATION_LIC_FILE,
    ACTIVATION_LIC_SERVER,
    ACTIVATION_SERIAL,
    ACTIVATION_TRIAL,
]

# silent.cfg parameter name for type of license activation (cfr. options listed above)
ACTIVATION_NAME = 'ACTIVATION_TYPE'  # since icc/ifort v2013_sp1, impi v4.1.1, imkl v11.1
# silent.cfg parameter name for install prefix
INSTALL_DIR_NAME = 'PSET_INSTALL_DIR'
# silent.cfg parameter name for install mode
INSTALL_MODE_NAME = 'PSET_MODE'
# Install mode since 2016 version
INSTALL_MODE = 'install'
# silent.cfg parameter name for license file/server specification
LICENSE_FILE_NAME = 'ACTIVATION_LICENSE_FILE'  # since icc/ifort v2013_sp1, impi v4.1.1, imkl v11.1
LICENSE_SERIAL_NUMBER = 'ACTIVATION_SERIAL_NUMBER'

COMP_ALL = 'ALL'
COMP_DEFAULTS = 'DEFAULTS'


class IntelBase(EasyBlock):
    """
    Base class for Intel software

    - no configure/make : binary release
    - add license_file variable
    """

    def __init__(self, *args, **kwargs):
        """Constructor, adds extra config options"""
        super().__init__(*args, **kwargs)

        self.license_file = 'UNKNOWN'
        self.license_env_var = 'UNKNOWN'

        # Initialise whether we need a runtime licence or not
        self.requires_runtime_license = True

        self.home_subdir = os.path.join(os.getenv('HOME'), 'intel')
        common_tmp_dir = os.path.dirname(tempfile.gettempdir())  # common tmp directory, same across nodes
        self.home_subdir_local = os.path.join(common_tmp_dir, os.environ.get('USER', 'nouser'), 'easybuild_intel')

        self.install_components = None
        # dictionary to keep track of "latest" directory symlinks
        # the target may only have major.minor, and tbb may have a lower version number than the compiler
        # for example compiler 2021.1.2 has tbb 2021.1.1, 2024.0.0 has directory name 2024.0
        self._latest_subdir = {}

    def get_versioned_subdir(self, subdir):
        """Return versioned directory that the 'latest' symlink points to in subdir"""
        if subdir not in self._latest_subdir:
            if os.path.islink(os.path.join(self.installdir, subdir, 'latest')):
                version = os.readlink(os.path.join(self.installdir, subdir, 'latest'))
            else:
                version = 'latest'
            latest_subdir = os.path.join(subdir, version)
            self._latest_subdir[subdir] = latest_subdir
            self.log.debug('Determined versioned directory for %s: %s', subdir, version)
        return self._latest_subdir[subdir]

    def set_versioned_subdir(self, subdir, path):
        """Set version-specific path for specified subdirectory."""
        self._latest_subdir[subdir] = path

    def get_guesses_tools(self):
        """Find reasonable paths for a subset of Intel tools, ignoring CPATH, LD_LIBRARY_PATH and LIBRARY_PATH"""
        self.log.deprecated("IntelBase.get_guesses_tools() is replaced by IntelBase.prepare_intel_tools_env()", '6.0')

    def prepare_intel_tools_env(self):
        """Find reasonable paths for a subset of Intel tools, ignoring CPATH, LD_LIBRARY_PATH and LIBRARY_PATH"""
        self.module_load_environment.PATH = [os.path.join(self.subdir, 'bin64')]
        self.module_load_environment.MANPATH = [os.path.join(self.subdir, 'man')]

        # make sure $CPATH, $LD_LIBRARY_PATH and $LIBRARY_PATH are not updated in generated module file,
        # because that leads to problem when the libraries included with VTune/Advisor/Inspector are being picked up
        mod_env_headers = self.module_load_environment.alias_vars(MODULE_LOAD_ENV_HEADERS)
        mod_env_libs = ['LD_LIBRARY_PATH', 'LIBRARY_PATH']
        for disallowed_var in mod_env_headers + mod_env_libs:
            self.module_load_environment.remove(disallowed_var)
            self.log.debug(f"Purposely not updating ${disallowed_var} in {self.name} module file")

    def get_custom_paths_tools(self, binaries):
        """Custom sanity check paths for certain Intel tools."""
        files = [os.path.join('bin64', b) for b in binaries]
        dirs = ['lib64', 'include']
        custom_paths = {
            'files': [os.path.join(self.subdir, f) for f in files],
            'dirs': [os.path.join(self.subdir, d) for d in dirs],
        }
        return custom_paths

    @staticmethod
    def extra_options(extra_vars=None):
        extra_vars = EasyBlock.extra_options(extra_vars)
        extra_vars.update({
            'license_activation': [ACTIVATION_LIC_SERVER, "License activation type", CUSTOM],
            'serial_number': [None, "Serial number for the product", CUSTOM],
            'requires_runtime_license': [True, "Boolean indicating whether or not a runtime license is required",
                                         CUSTOM],
            'components': [None, "List of components to install", CUSTOM],
        })

        return extra_vars

    def parse_components_list(self):
        """parse the regex in the components extra_options and select the matching components
        from the mediaconfig.xml file in the install dir"""

        mediaconfigpath = os.path.join(self.cfg['start_dir'], 'pset', 'mediaconfig.xml')
        if not os.path.isfile(mediaconfigpath):
            raise EasyBuildError("Could not find %s to find list of components." % mediaconfigpath)

        mediaconfig = read_file(mediaconfigpath)
        available_components = re.findall("<Abbr>(?P<component>[^<]+)</Abbr>", mediaconfig, re.M)
        self.log.debug("Intel components found: %s" % available_components)
        self.log.debug("Using regex list: %s" % self.cfg['components'])

        if COMP_ALL in self.cfg['components'] or COMP_DEFAULTS in self.cfg['components']:
            if len(self.cfg['components']) == 1:
                self.install_components = self.cfg['components']
            else:
                raise EasyBuildError("If you specify %s as components, you cannot specify anything else: %s",
                                     ' or '.join([COMP_ALL, COMP_DEFAULTS]), self.cfg['components'])
        else:
            self.install_components = []
            for comp_regex in self.cfg['components']:
                comps = [comp for comp in available_components if re.match(comp_regex, comp)]
                self.install_components.extend(comps)

        self.log.debug("Components to install: %s" % self.install_components)

    def clean_home_subdir(self):
        """Remove contents of (local) 'intel' directory home subdir, where stuff is cached."""
        if os.path.exists(self.home_subdir_local):
            self.log.debug("Cleaning up %s..." % self.home_subdir_local)
            try:
                for tree in os.listdir(self.home_subdir_local):
                    self.log.debug("... removing %s subtree" % tree)
                    path = os.path.join(self.home_subdir_local, tree)
                    if os.path.isfile(path) or os.path.islink(path):
                        remove_file(path)
                    else:
                        shutil.rmtree(path)
            except OSError as err:
                raise EasyBuildError("Cleaning up intel dir %s failed: %s", self.home_subdir_local, err)

    def setup_local_home_subdir(self):
        """
        Intel script use $HOME/intel to cache stuff.
        To enable parallel builds, we symlink $HOME/intel to a temporary dir on the local disk."""

        try:
            # make sure local directory exists
            if not os.path.exists(self.home_subdir_local):
                os.makedirs(self.home_subdir_local)
                self.log.debug("Created local dir %s" % self.home_subdir_local)

            if os.path.exists(self.home_subdir):
                # if 'intel' dir in $HOME already exists, make sure it's the right symlink
                symlink_ok = os.path.islink(self.home_subdir) and os.path.samefile(self.home_subdir,
                                                                                   self.home_subdir_local)
                if not symlink_ok:
                    # rename current 'intel' dir
                    home_intel_bk = tempfile.mkdtemp(dir=os.path.dirname(self.home_subdir),
                                                     prefix='%s.bk.' % os.path.basename(self.home_subdir))
                    self.log.info("Moving %(ih)s to %(ihl)s, I need %(ih)s myself..." % {'ih': self.home_subdir,
                                                                                         'ihl': home_intel_bk})
                    shutil.move(self.home_subdir, home_intel_bk)

                    # set symlink in place
                    os.symlink(self.home_subdir_local, self.home_subdir)
                    self.log.debug("Created symlink (1) %s to %s" % (self.home_subdir, self.home_subdir_local))

            else:
                # if a broken symlink is present, remove it first
                if os.path.islink(self.home_subdir):
                    remove_file(self.home_subdir)
                os.symlink(self.home_subdir_local, self.home_subdir)
                self.log.debug("Created symlink (2) %s to %s" % (self.home_subdir, self.home_subdir_local))

        except OSError as err:
            raise EasyBuildError("Failed to symlink %s to %s: %s", self.home_subdir_local, self.home_subdir, err)

    def prepare_step(self, *args, **kwargs):
        """Custom prepare step for IntelBase. Set up the license"""
        requires_runtime_license = kwargs.pop('requires_runtime_license', True)

        super().prepare_step(*args, **kwargs)

        # Decide if we need a license or not (default is True because of defaults of individual Booleans)
        self.requires_runtime_license = self.cfg['requires_runtime_license'] and requires_runtime_license
        self.serial_number = self.cfg['serial_number']

        if self.serial_number:
            self.log.info("Using provided serial number (%s) and ignoring other licenses", self.serial_number)
        elif self.requires_runtime_license:
            default_lic_env_var = 'INTEL_LICENSE_FILE'
            license_specs = ensure_iterable_license_specs(self.cfg['license_file'])
            lic_specs, self.license_env_var = find_flexlm_license(custom_env_vars=[default_lic_env_var],
                                                                  lic_specs=license_specs)

            if lic_specs:
                if self.license_env_var is None:
                    self.log.info("Using Intel license specifications from 'license_file': %s", lic_specs)
                    self.license_env_var = default_lic_env_var
                else:
                    self.log.info("Using Intel license specifications from $%s: %s", self.license_env_var, lic_specs)

                self.license_file = os.pathsep.join(lic_specs)
                env.setvar(self.license_env_var, self.license_file)

                # if we have multiple retained lic specs, specify to 'use a license which exists on the system'
                if len(lic_specs) > 1:
                    self.log.debug("More than one license specs found, using '%s' license activation instead of "
                                   "'%s'", ACTIVATION_EXIST_LIC, self.cfg['license_activation'])
                    self.cfg['license_activation'] = ACTIVATION_EXIST_LIC

                    # $INTEL_LICENSE_FILE should always be set during installation with existing license
                    env.setvar(default_lic_env_var, self.license_file)
            else:
                msg = "No viable license specifications found; "
                msg += "specify 'license_file', or define $INTEL_LICENSE_FILE or $LM_LICENSE_FILE"
                raise EasyBuildError(msg)

    def configure_step(self):
        """Configure: handle license file and clean home dir."""

        # prepare (local) 'intel' home subdir
        self.setup_local_home_subdir()
        self.clean_home_subdir()

        # determine list of components, based on 'components' easyconfig parameter (if specified)
        if self.cfg['components']:
            self.parse_components_list()
        else:
            self.log.debug("No components specified")

    def build_step(self):
        """Binary installation files, so no building."""
        pass

    def install_step_classic(self, silent_cfg_names_map=None, silent_cfg_extras=None):
        """Actual installation for versions prior to 2021.x

        - create silent cfg file
        - set environment parameters
        - execute command
        """
        if silent_cfg_names_map is None:
            silent_cfg_names_map = {}

        if self.serial_number or self.requires_runtime_license:
            lic_entry = ""
            if self.serial_number:
                lic_entry = "%(license_serial_number)s=%(serial_number)s"
                self.cfg['license_activation'] = ACTIVATION_SERIAL
            else:
                # license file entry is only applicable with license file or server type of activation
                # also check whether specified activation type makes sense
                lic_file_server_activations = [ACTIVATION_EXIST_LIC, ACTIVATION_LIC_FILE, ACTIVATION_LIC_SERVER]
                other_activations = [act for act in ACTIVATION_TYPES if act not in lic_file_server_activations]
                if self.cfg['license_activation'] in lic_file_server_activations:
                    lic_entry = "%(license_file_name)s=%(license_file)s"
                elif not self.cfg['license_activation'] in other_activations:
                    raise EasyBuildError("Unknown type of activation specified: %s (known :%s)",
                                         self.cfg['license_activation'], ACTIVATION_TYPES)
            silent = '\n'.join([
                "%(activation_name)s=%(activation)s",
                lic_entry,
                ""  # Add a newline at the end, so we can easily append if needed
            ]) % {
                'activation_name': silent_cfg_names_map.get('activation_name', ACTIVATION_NAME),
                'activation': self.cfg['license_activation'],
                'license_file_name': silent_cfg_names_map.get('license_file_name', LICENSE_FILE_NAME),
                'license_file': self.license_file,
                'license_serial_number': silent_cfg_names_map.get('license_serial_number', LICENSE_SERIAL_NUMBER),
                'serial_number': self.serial_number,
            }
        else:
            self.log.debug("No license required, so not including license specifications in silent.cfg")
            silent = ''

        silent += '\n'.join([
            "%(install_dir_name)s=%(install_dir)s",
            "ACCEPT_EULA=accept",
            "%(install_mode_name)s=%(install_mode)s",
            "CONTINUE_WITH_OPTIONAL_ERROR=yes",
            ""  # Add a newline at the end, so we can easily append if needed
        ]) % {
            'install_dir_name': silent_cfg_names_map.get('install_dir_name', INSTALL_DIR_NAME),
            'install_dir': silent_cfg_names_map.get('install_dir', self.installdir),
            'install_mode': silent_cfg_names_map.get('install_mode', INSTALL_MODE),
            'install_mode_name': silent_cfg_names_map.get('install_mode_name', INSTALL_MODE_NAME),
        }

        if self.install_components is not None:
            if len(self.install_components) == 1 and self.install_components[0] in [COMP_ALL, COMP_DEFAULTS]:
                # no quotes should be used for ALL or DEFAULTS
                silent += 'COMPONENTS=%s\n' % self.install_components[0]
            elif self.install_components:
                # a list of components is specified (needs quotes)
                components = ';'.join(self.install_components)
                if LooseVersion(self.version) >= LooseVersion('2017'):
                    # for versions 2017.x and newer, double quotes should not be there...
                    silent += 'COMPONENTS=%s\n' % components
                else:
                    silent += 'COMPONENTS="%s"\n' % components
            else:
                raise EasyBuildError("Empty list of matching components obtained via %s", self.cfg['components'])

        if silent_cfg_extras is not None:
            if isinstance(silent_cfg_extras, dict):
                silent += '\n'.join("%s=%s" % (key, value) for (key, value) in silent_cfg_extras.items())
            else:
                raise EasyBuildError("silent_cfg_extras needs to be a dict")

        # we should be already in the correct directory
        silentcfg = os.path.join(os.getcwd(), 'silent.cfg')
        write_file(silentcfg, silent)
        self.log.debug("Contents of %s:\n%s", silentcfg, silent)

        # set some extra env variables
        env.setvar('LOCAL_INSTALL_VERBOSE', '1')
        env.setvar('VERBOSE_MODE', '1')

        env.setvar('INSTALL_PATH', self.installdir)

        # perform installation
        cmd = ' '.join([
            self.cfg['preinstallopts'],
            './install.sh',
            '-s ' + silentcfg,
            self.cfg['installopts'],
        ])

        run_shell_cmd(cmd)

    def install_step_oneapi(self, *args, **kwargs):
        """
        Actual installation for versions 2021.x onwards.
        """
        # require that EULA is accepted
        intel_eula_url = 'https://software.intel.com/content/www/us/en/develop/articles/end-user-license-agreement.html'
        self.check_accepted_eula(name='Intel-oneAPI', more_info=intel_eula_url)

        # exactly one "source" file is expected: the (offline) installation script
        if len(self.src) == 1:
            install_script = self.src[0]['name']
        else:
            src_fns = ', '.join([x['name'] for x in self.src])
            raise EasyBuildError("Expected to find exactly one 'source' file (installation script): %s", src_fns)

        adjust_permissions(install_script, stat.S_IXUSR)

        # see https://software.intel.com/content/www/us/en/develop/documentation/...
        # .../installation-guide-for-intel-oneapi-toolkits-linux/top/...
        # .../local-installer-full-package/install-with-command-line.html
        cmd = [
            self.cfg['preinstallopts'],
            './' + install_script,
            '-a',  # required to specify that following are options for installer
            '--action install',
            '--silent',
            '--eula accept',
            '--install-dir ' + self.installdir,
        ]

        if self.install_components:
            cmd.extend([
                '--components',
                ':'.join(self.install_components),
            ])

        cmd.append(self.cfg['installopts'])

        run_shell_cmd(' '.join(cmd))

    def install_step(self, *args, **kwargs):
        """
        Install Intel software
        """
        if LooseVersion(self.version) >= LooseVersion('2021'):
            self.install_step_oneapi(*args, **kwargs)
        else:
            self.install_step_classic(*args, **kwargs)

    def move_after_install(self):
        """Move installed files to correct location after installation."""
        subdir = os.path.join(self.installdir, self.name, self.version)
        self.log.debug("Moving contents of %s to %s" % (subdir, self.installdir))
        try:
            # remove senseless symlinks, e.g. impi_5.0.1 and impi_latest
            majver = '.'.join(self.version.split('.')[:-1])
            for symlink in ['%s_%s' % (self.name, majver), '%s_latest' % self.name]:
                symlink_fp = os.path.join(self.installdir, symlink)
                if os.path.exists(symlink_fp):
                    remove_file(symlink_fp)
            # move contents of 'impi/<version>' dir to installdir
            for fil in os.listdir(subdir):
                source = os.path.join(subdir, fil)
                target = os.path.join(self.installdir, fil)
                self.log.debug("Moving %s to %s" % (source, target))
                shutil.move(source, target)
            shutil.rmtree(os.path.join(self.installdir, self.name))
        except OSError as err:
            raise EasyBuildError("Failed to move contents of %s to %s: %s", subdir, self.installdir, err)

    def sanity_check_rpath(self):
        """Skip the rpath sanity check, this is binary software"""
        self.log.info("RPATH sanity check is skipped when using %s easyblock (derived from IntelBase)",
                      self.__class__.__name__)

    def make_module_extra(self, *args, **kwargs):
        """Custom variable definitions in module file."""
        txt = super().make_module_extra(*args, **kwargs)

        if self.requires_runtime_license:
            txt += self.module_generator.prepend_paths(self.license_env_var, [self.license_file],
                                                       allow_abs=True, expand_relpaths=False)

        return txt

    def cleanup_step(self):
        """Cleanup leftover mess

        - clean home dir
        - generic cleanup (get rid of build dir)
        """
        self.clean_home_subdir()

        super().cleanup_step()

    # no default sanity check, needs to be implemented by derived class
