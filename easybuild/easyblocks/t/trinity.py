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
EasyBuild support for building and installing Trinity, implemented as an easyblock

@author: Stijn De Weirdt (Ghent University)
@author: Dries Verdegem (Ghent University)
@author: Kenneth Hoste (Ghent University)
@author: Pieter De Baets (Ghent University)
@author: Jens Timmerman (Ghent University)
@author: Balazs Hajgato (Vrije Universiteit Brussel)
@author: Robert Qiao (DeepThought HPC Service, Flinders University, Adelaide, Australia)
"""
import glob
import os
import shutil
from easybuild.tools import LooseVersion

import easybuild.tools.toolchain as toolchain
from easybuild.framework.easyblock import EasyBlock
from easybuild.framework.easyconfig import CUSTOM
from easybuild.tools.build_log import EasyBuildError
from easybuild.tools.environment import setvar
from easybuild.tools.filetools import apply_regex_substitutions
from easybuild.tools.modules import get_software_root
from easybuild.tools.run import run_shell_cmd


class EB_Trinity(EasyBlock):
    """Support for building/installing Trinity."""

    def __init__(self, *args, **kwargs):
        """Initialisation of custom class variables for Trinity."""
        EasyBlock.__init__(self, *args, **kwargs)

        self.build_in_installdir = True

        version = LooseVersion(self.version)
        if version >= LooseVersion('2.0') and version < LooseVersion('2.3'):
            sep = '-'
        elif version >= LooseVersion('2.3') and version < LooseVersion('2.9'):
            sep = '-Trinity-v'
        elif version >= LooseVersion('2.9') and version < LooseVersion('3.0'):
            sep = '-v'
        else:
            sep = '_r'
        self.trinityrnaseq_subdir = f'trinityrnaseq{sep}{self.version}'

        self.module_load_environment.PATH = self.trinityrnaseq_subdir
        self.module_load_environment.TRINITY_HOME = self.trinityrnaseq_subdir

    @staticmethod
    def extra_options():
        """Custom easyconfig parameters for Trinity."""
        extra_vars = {
            'withsampledata': [False, "Include sample data", CUSTOM],
            'bwapluginver': [None, "BWA pugin version", CUSTOM],
            'RSEMmod': [False, "Enable RSEMmod", CUSTOM],
        }
        return EasyBlock.extra_options(extra_vars)

    def butterfly(self):
        """Install procedure for Butterfly."""

        self.log.info("Begin Butterfly")

        setvar("JAVA_TOOL_OPTIONS", "-Dfile.encoding=UTF8")

        dst = os.path.join(self.cfg['start_dir'], 'Butterfly', 'src')
        try:
            os.chdir(dst)
        except OSError as err:
            raise EasyBuildError("Butterfly: failed to change to dst dir %s: %s", dst, err)

        run_shell_cmd("ant")

        self.log.info("End Butterfly")

    def chrysalis(self, run=True):
        """Install procedure for Chrysalis."""

        make_flags = "COMPILER='%s' CPLUSPLUS='%s' CC='%s' " % (os.getenv('CXX'),
                                                                os.getenv('CXX'),
                                                                os.getenv('CC'))
        make_flags += "OMP_FLAGS='%s' OMP_LINK='%s' " % (self.toolchain.get_flag('openmp'),
                                                         os.getenv('LIBS'))
        make_flags += "OPTIM='-O1' SYS_OPT='-O2 %s' " % self.toolchain.get_flag('optarch')
        make_flags += "OPEN_MP=yes UNSUPPORTED=yes DEBUG=no QUIET=yes"

        if run:
            self.log.info("Begin Chrysalis")

            dst = os.path.join(self.cfg['start_dir'], 'Chrysalis')
            try:
                os.chdir(dst)
            except OSError as err:
                raise EasyBuildError("Chrysalis: failed to change to dst dir %s: %s", dst, err)

            run_shell_cmd("make clean")
            run_shell_cmd("make %s" % make_flags)

            self.log.info("End Chrysalis")

        else:
            return make_flags

    def inchworm(self, run=True):
        """Install procedure for Inchworm."""

        make_flags = 'CXXFLAGS="%s %s"' % (os.getenv('CXXFLAGS'), self.toolchain.get_flag('openmp'))
        version = LooseVersion(self.version)
        if version >= LooseVersion('2.0') and version < LooseVersion('3.0'):
            make_flags += ' CXX=%s' % os.getenv('CXX')

        if run:
            self.log.info("Begin Inchworm")

            dst = os.path.join(self.cfg['start_dir'], 'Inchworm')
            try:
                os.chdir(dst)
            except OSError as err:
                raise EasyBuildError("Inchworm: failed to change to dst dir %s: %s", dst, err)

            run_shell_cmd('./configure --prefix=%s' % dst)
            run_shell_cmd("make install %s" % make_flags)

            self.log.info("End Inchworm")

        else:
            return make_flags

    def jellyfish(self):
        """use a seperate jellyfish source if it exists, otherwise, just install the bundled jellyfish"""
        self.log.debug("begin jellyfish")
        self.log.debug("startdir: %s", self.cfg['start_dir'])
        cwd = os.getcwd()
        glob_pat = os.path.join(self.cfg['start_dir'], "..", "jellyfish-*")
        jellyfishdirs = glob.glob(glob_pat)
        self.log.debug("glob pattern '%s' yields %s" % (glob_pat, jellyfishdirs))
        if len(jellyfishdirs) == 1 and os.path.isdir(jellyfishdirs[0]):
            jellyfishdir = jellyfishdirs[0]
            # if there is a jellyfish directory
            self.log.info("detected jellyfish directory %s, so using this source", jellyfishdir)
            orig_jellyfishdir = os.path.join(self.cfg['start_dir'], 'trinity-plugins', 'jellyfish')
            try:
                # remove original symlink
                os.unlink(orig_jellyfishdir)
            except OSError as err:
                self.log.warning("jellyfish plugin: failed to remove dir %s: %s" % (orig_jellyfishdir, err))
            try:
                # create new one
                os.symlink(jellyfishdir, orig_jellyfishdir)
                os.chdir(orig_jellyfishdir)
            except OSError as err:
                raise EasyBuildError("jellyfish plugin: failed to change dir %s: %s", orig_jellyfishdir, err)

            run_shell_cmd('./configure --prefix=%s' % orig_jellyfishdir)
            cmd = "make CC='%s' CXX='%s' CFLAGS='%s'" % (os.getenv('CC'), os.getenv('CXX'), os.getenv('CFLAGS'))
            run_shell_cmd(cmd)

            # the installstep is running the jellyfish script, this is a wrapper that will compile .lib/jellyfish
            run_shell_cmd("bin/jellyfish cite")

            # return to original dir
            try:
                os.chdir(cwd)
            except OSError:
                raise EasyBuildError("jellyfish: Could not return to original dir %s", cwd)
        elif jellyfishdirs:
            raise EasyBuildError("Found multiple 'jellyfish-*' directories: %s", jellyfishdirs)
        else:
            self.log.info("no seperate source found for jellyfish, letting Makefile build shipped version")

        self.log.debug("end jellyfish")

    def kmer(self):
        """Install procedure for kmer (Meryl)."""

        self.log.info("Begin Meryl")

        dst = os.path.join(self.cfg['start_dir'], 'trinity-plugins', 'kmer')
        try:
            os.chdir(dst)
        except OSError as err:
            raise EasyBuildError("Meryl: failed to change to dst dir %s: %s", dst, err)

        cmd = "./configure.sh"
        run_shell_cmd(cmd)

        cmd = 'make -j 1 CCDEP="%s -MM -MG" CXXDEP="%s -MM -MG"' % (os.getenv('CC'), os.getenv('CXX'))
        run_shell_cmd(cmd)

        cmd = 'make install'
        run_shell_cmd(cmd)

        self.log.info("End Meryl")

    def trinityplugin(self, plugindir, cc=None):
        """Install procedure for Trinity plugins."""

        self.log.info("Begin %s plugin" % plugindir)

        dst = os.path.join(self.cfg['start_dir'], 'trinity-plugins', plugindir)
        try:
            os.chdir(dst)
        except OSError as err:
            raise EasyBuildError("%s plugin: failed to change to dst dir %s: %s", plugindir, dst, err)

        if not cc:
            cc = os.getenv('CC')

        cmd = "make CC='%s' CXX='%s' CFLAGS='%s'" % (cc, os.getenv('CXX'), os.getenv('CFLAGS'))
        run_shell_cmd(cmd)

        self.log.info("End %s plugin" % plugindir)

    def configure_step(self):
        """No configuration for Trinity."""

        pass

    def build_step(self):
        """No building for Trinity."""

        pass

    def install_step(self):
        """Custom install procedure for Trinity."""

        version = LooseVersion(self.version)
        if version > LooseVersion('2012') and version < LooseVersion('2012-10-05'):
            self.inchworm()
            self.chrysalis()
            self.kmer()
            if version < LooseVersion('2.9'):
                self.butterfly()

            bwapluginver = self.cfg['bwapluginver']
            if bwapluginver:
                self.trinityplugin('bwa-%s-patched_multi_map' % bwapluginver)

            if self.cfg['RSEMmod']:
                self.trinityplugin('RSEM-mod', cc=os.getenv('CXX'))

        else:
            self.jellyfish()

            inchworm_flags = self.inchworm(run=False)
            chrysalis_flags = self.chrysalis(run=False)

            cc = os.getenv('CC')
            cxx = os.getenv('CXX')

            lib_flags = ""
            for lib in ['ncurses', 'zlib']:
                libroot = get_software_root(lib)
                if libroot:
                    lib_flags += " -L%s/lib" % libroot

            if version >= LooseVersion('2.0') and version < LooseVersion('3.0'):
                regex_subs = [
                    (r'^( INCHWORM_CONFIGURE_FLAGS\s*=\s*).*$', r'\1%s' % inchworm_flags),
                    (r'^( CHRYSALIS_MAKE_FLAGS\s*=\s*).*$', r'\1%s' % chrysalis_flags),
                ]
            else:
                regex_subs = [
                    (r'^(INCHWORM_CONFIGURE_FLAGS\s*=\s*).*$', r'\1%s' % inchworm_flags),
                    (r'^(CHRYSALIS_MAKE_FLAGS\s*=\s*).*$', r'\1%s' % chrysalis_flags),
                    (r'(/rsem && \$\(MAKE\))\s*$',
                     r'\1 CC=%s CXX="%s %s" CFLAGS_EXTRA="%s"\n' % (cc, cxx, lib_flags, lib_flags)),
                    (r'(/fastool && \$\(MAKE\))\s*$',
                     r'\1 CC="%s -std=c99" CFLAGS="%s ${CFLAGS}"\n' % (cc, lib_flags)),
                ]
            apply_regex_substitutions('Makefile', regex_subs)

            trinity_compiler = None
            comp_fam = self.toolchain.comp_family()
            if comp_fam in [toolchain.INTELCOMP]:
                trinity_compiler = "intel"
            elif comp_fam in [toolchain.GCC]:
                trinity_compiler = "gcc"
            else:
                raise EasyBuildError("Don't know how to set TRINITY_COMPILER for %s compiler", comp_fam)

            explicit_make_args = ''
            if version >= LooseVersion('2.0') and version < LooseVersion('3.0'):
                explicit_make_args = 'all plugins'

            cmd = "make TRINITY_COMPILER=%s %s" % (trinity_compiler, explicit_make_args)
            run_shell_cmd(cmd)

            # butterfly is not included in standard build before v2.9.0
            if version < LooseVersion('2.9'):
                self.butterfly()

        # remove sample data if desired
        if not self.cfg['withsampledata']:
            try:
                shutil.rmtree(os.path.join(self.cfg['start_dir'], 'sample_data'))
            except OSError as err:
                raise EasyBuildError("Failed to remove sample data: %s", err)

    def sanity_check_step(self):
        """Custom sanity check for Trinity."""

        version = LooseVersion(self.version)
        # Chrysalis
        if version >= LooseVersion('2.9') and version < LooseVersion('2000'):
            chrysalis_bin = os.path.join('Chrysalis', 'bin')
            chrysalis_files = ['BubbleUpClustering',
                               'CreateIwormFastaBundle',
                               'QuantifyGraph',
                               'Chrysalis',
                               'GraphFromFasta',
                               'ReadsToTranscripts']
        elif version >= LooseVersion('2.8') and version < LooseVersion('2.9'):
            chrysalis_bin = os.path.join('Chrysalis', 'bin')
            chrysalis_files = ['Chrysalis']
        else:
            chrysalis_bin = 'Chrysalis'
            chrysalis_files = ['Chrysalis']
        chrysalis_bin_files = [os.path.join(chrysalis_bin, x) for x in chrysalis_files]

        # Inchworm
        inchworm_bin = os.path.join('Inchworm', 'bin')
        inchworm_files = ['inchworm']
        if version >= LooseVersion('2.9') and version < LooseVersion('2000'):
            inchworm_files.extend(['FastaToDeBruijn', 'fastaToKmerCoverageStats'])
        inchworm_bin_files = [os.path.join(inchworm_bin, x) for x in inchworm_files]

        # folders path
        dir_path = ['util']
        if version < LooseVersion('2.9'):
            dir_path.append(os.path.join('Butterfly', 'src', 'bin'))

        # these lists are definitely non-exhaustive, but better than nothing
        custom_paths = {
            'files': [os.path.join(self.trinityrnaseq_subdir, x) for x in (inchworm_bin_files + chrysalis_bin_files)],
            'dirs': [os.path.join(self.trinityrnaseq_subdir, x) for x in dir_path]
        }

        custom_commands = ["Trinity --version | grep 'Trinity version'"]

        super().sanity_check_step(custom_commands=custom_commands, custom_paths=custom_paths)
