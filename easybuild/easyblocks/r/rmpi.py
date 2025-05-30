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
EasyBuild support for building and installing the Rmpi R library, implemented as an easyblock

@author: Stijn De Weirdt (Ghent University)
@author: Dries Verdegem (Ghent University)
@author: Kenneth Hoste (Ghent University)
@author: Jens Timmerman (Ghent University)
@author: Toon Willems (Ghent University)
@author: Balazs Hajgato (Vrije Universiteit Brussel)
"""
import easybuild.tools.toolchain as toolchain
from easybuild.tools import LooseVersion
from easybuild.easyblocks.generic.rpackage import RPackage


class EB_Rmpi(RPackage):
    """Build and install Rmpi R library."""

    def prepare_rmpi_configureargs(self):
        """
        Prepare configure arguments for installing Rpmi.
        """

        mpi_types = {
            toolchain.MPI_TYPE_OPENMPI: "OPENMPI",
            toolchain.MPI_TYPE_MPICH: "MPICH",
            # toolchain.MPI_TYPE_LAM: "LAM",  # no support for LAM yet
        }
        # type of MPI
        # MPI_TYPE does not distinguish between MPICH and IntelMPI, which is why we also check mpi_family()
        mpi_type = self.toolchain.mpi_family()
        rmpi_type = mpi_types[self.toolchain.MPI_TYPE]
        # Rmpi versions 0.6-4 and up support INTELMPI (using --with-Rmpi-type=INTELMPI)
        if ((LooseVersion(self.version) >= LooseVersion('0.6-4')) and (mpi_type == toolchain.INTELMPI)):
            rmpi_type = 'INTELMPI'

        self.log.debug("Setting configure args for Rmpi")
        self.configureargs = [
            "--with-Rmpi-include=%s" % self.toolchain.get_variable('MPI_INC_DIR'),
            "--with-Rmpi-libpath=%s" % self.toolchain.get_variable('MPI_LIB_DIR'),
            "--with-mpi=%s" % self.toolchain.get_software_root(self.toolchain.MPI_MODULE_NAME)[0],
            "--with-Rmpi-type=%s" % rmpi_type,
        ]

    def install_extension(self):
        """
        Install Rmpi as extension, after seting various configure arguments.
        """
        self.prepare_rmpi_configureargs()
        # it might be needed to get the R cmd and run it with mympirun...
        super().install_extension()

    def install_extension_async(self, *args, **kwargs):
        """
        Asynchronously install Rmpi as extension, after seting various configure arguments.
        """
        self.prepare_rmpi_configureargs()
        # it might be needed to get the R cmd and run it with mympirun...
        return super().install_extension_async(*args, **kwargs)
