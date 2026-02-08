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

from typing import Optional

from .disassembler.types import Instruction, ImmediateOperand

def get_delay_consumption(instr:Instruction):
    delay_slots = 1
    if instr.is_fp_header():
        delay_slots = 0
    elif instr.opcode == 'nop':
        assert isinstance(instr.operands[0], ImmediateOperand)
        delay_slots = instr.operands[0].value
    elif instr.opcode == 'idle':
        # in theory unlimited, but binja limits delay to 255
        delay_slots = 255
    elif instr.opcode == 'addkpc':
        assert isinstance(instr.operands[2], ImmediateOperand)
        delay_slots = instr.operands[2].value + 1
    elif instr.opcode == 'bnop':
        assert isinstance(instr.operands[1], ImmediateOperand)
        delay_slots = instr.operands[1].value + 1
    elif instr.opcode == 'callp':
        delay_slots += 5 # implied NOP cycles after instruction
    elif (instr.opcode.startswith('ld')
            and instr.header is not None
            and instr.header.protected_loads):
        delay_slots += 4 # NOP cycles after instruction
    return delay_slots

class UnwrapError(ValueError):
    '''Unwrapping of None value.'''
    pass

def unwrap[T](obj: Optional[T]) -> T:
    if obj is None: raise UnwrapError
    return obj

