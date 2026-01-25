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

from .arch import TMS320C67x, C67Call, TMS320C6x


TMS320C67x.register()
_arch = _bn.architecture.Architecture['TMS320C67x+']
_cc = C67Call(arch=_arch, name='C67call')
_arch.register_calling_convention(_cc)
_arch.default_calling_convention = _cc
_bn.binaryview.BinaryViewType['ELF'].register_arch(
    0x9c60, _bn.enums.Endianness.LittleEndian, _arch
)

TMS320C6x.register()
_arch = _bn.architecture.Architecture['TMS320C6x']
_bn.binaryview.BinaryViewType['ELF'].register_arch(
    140, _bn.enums.Endianness.LittleEndian, _arch
)
