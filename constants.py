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

ARCH_SIZE = 4
DW_SIZE = 2*ARCH_SIZE
HW_SIZE = ARCH_SIZE//2
FP_SIZE = 8*ARCH_SIZE
LOAD_BASE = 0x400

BRANCH_DELAY = 5
INSTRUCTION_DELAY = {
    'b': 5,
    'ldb': 4,
    'ldw': 4,
    'mpyi': 8,
    'stb': 4,
    'stw': 4,
}
