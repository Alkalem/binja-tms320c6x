# Copyright 2025-2026 Benedikt Waibel
# 
# This file is part of the binary ninja tms320c6x architecture plugin.
# 
# This plugin is free software: 
# you can redistribute it and/or modify it under the terms of the GNU General
# Public License as published by the Free Software Foundation, either version 3
# of the License, or (at your option) any later version.
# 
# This program is distributed in the hope that it will be useful,but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License along with
# this program. If not, see <http://www.gnu.org/licenses/>.

import binaryninja as _bn

from .arch import TInyC6x, TMS320C6x
from .platform import TInyC6xCall, LinuxC6xPlatform, C6xCall


def _init_plugin():
    TINY_C_MACHINE = 0x9c60
    TI_C6x_MACHINE = 140
    SYSV_OSABI = 0
    LINUX_OSABI = 3

    TInyC6x.register()
    arch = _bn.architecture.Architecture['TInyC6x']
    cc = TInyC6xCall(arch=arch, name='TInyC6xcall')
    arch.register_calling_convention(cc)
    arch.default_calling_convention = cc
    _bn.binaryview.BinaryViewType['ELF'].register_arch(
        TINY_C_MACHINE, _bn.enums.Endianness.LittleEndian, arch
    )

    TMS320C6x.register()
    arch = _bn.architecture.Architecture['TMS320C6x']
    cc = C6xCall(arch=arch, name='C6xCall')
    arch.register_calling_convention(cc)
    arch.default_calling_convention = cc
    _bn.binaryview.BinaryViewType['ELF'].register_arch(
        TI_C6x_MACHINE, _bn.enums.Endianness.LittleEndian, arch
    )

    platform = LinuxC6xPlatform(arch)
    platform.register("linux")
    platform.default_calling_convention = cc

    # Linux uses ELF platforms 0 and 3, so register for both
    _bn.BinaryViewType['ELF'].register_platform(SYSV_OSABI, arch, platform)
    _bn.BinaryViewType['ELF'].register_platform(LINUX_OSABI, arch, platform)
    return platform

# Platform currently needs to be stored in the module.
# TODO: Should be removed when platform registration prevents freeing it.
__platform = _init_plugin()

