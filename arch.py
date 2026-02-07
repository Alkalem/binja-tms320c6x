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

from binaryninja.architecture import Architecture, RegisterInfo, \
    RegisterName, BasicBlockAnalysisContext, InstructionTextToken
from binaryninja.callingconvention import CallingConvention
from binaryninja.enums import ImplicitRegisterExtend
from binaryninja.function import Function
from binaryninja.log import log_warn

from typing import Any, Optional

from .disassembler.types import Register, ControlRegister, ISA
from .analysis import analyze_basic_blocks
from .instruction import Disassembler, gen_tokens, gen_parallel_fallthrough
from .constants import *
from .lifting import lift_instructions


class TMS320C6xBaseArch(Architecture):
    address_size = ARCH_SIZE        # 32-bit addresses
    default_int_size = ARCH_SIZE    # 4-byte integers
    instr_alignment = ARCH_SIZE     # fixed 4 byte alignment

    # Work around: include possible branch delay instructions
    # (see binaryninja-api issue 6868)
    max_instr_length = ARCH_SIZE * (8*(5+1))

    disasm:Disassembler

    def get_instruction_info(self, data, addr):
        return self.disasm.info(data, addr)
    
    def get_instruction_low_level_il(self, data, addr, il):
        stream = self.disasm.disasm(data, addr)
        return lift_instructions(self, il, stream)

    def analyze_basic_blocks(self, func: Function, 
            context: BasicBlockAnalysisContext) -> None:
        analyze_basic_blocks(self, func, context)

class TMS320C67x(TMS320C6xBaseArch):
    name = 'TMS320C67x+'

    regs = dict()
    system_regs = list()
    for reg in Register:
        if reg & 16: continue # skip high registers
        _name = RegisterName(reg.name)
        regs[_name] = RegisterInfo(_name, ARCH_SIZE)
        _nameH = RegisterName(reg.name+'H')
        regs[_nameH] = RegisterInfo(_name, ARCH_SIZE//2, ARCH_SIZE//2)
    for reg in ControlRegister:
        if reg.isa not in ISA.C67XP: continue
        _name = RegisterName(reg.name)
        regs[_name] = RegisterInfo(_name, ARCH_SIZE)
        system_regs.append(_name)

    stack_pointer = 'B15'
    link_reg = 'B3'
    
    disasm = Disassembler(isa=ISA.C67XP) 

    def get_instruction_text(self, data, addr):
        instructions = self.disasm.disasm(data, addr, limit=8)
        tokens = []
        parallel = False
        for i, instruction in enumerate(instructions):
            assert instruction.size == ARCH_SIZE
            tokens.extend(gen_tokens(instruction, i*ARCH_SIZE, parallel))
            # separate execution packets
            parallel = instruction.parallel
            if not parallel: break
            # stop at fetch packet boundary
            if ((instruction.address+ARCH_SIZE) % (8*ARCH_SIZE) == 0): break
        return tokens, ARCH_SIZE * (i+1)

class C67Call(CallingConvention):
    name = 'c67call'

    caller_saved_regs = [
        'A0', 'B0', 'A1', 'B1', 'A2', 'B2', 'A3', 
        'A4', 'B4', 'A5', 'B5', 'A6', 'B6', 'A7', 'B7',
        'A8', 'B8', 'A9', 'B9', 'A10', 'B10', 'A11', 'B11',
        'A12', 'B12', 'A13', 'B13', 'A14', 'B14'
    ]
    callee_saved_regs = [
        'B3', 'A15', 'B15'
    ]
    int_arg_regs = [
        'A4', 'B4', 'A6', 'B6', 'A8', 'B8'
    ]

    eligible_for_heuristics = True
    int_return_reg = 'A4'
    high_int_return_reg = 'A5'

class TMS320C6x(TMS320C6xBaseArch):
    name = 'TMS320C6x'
    # instr_alignment = HW_SIZE     # compact instructions

    regs = dict()
    system_regs = list()
    for reg in Register:
        _name = RegisterName(reg.name)
        regs[_name] = RegisterInfo(_name, ARCH_SIZE)
        _nameH = RegisterName(reg.name+'H')
        regs[_nameH] = RegisterInfo(_name, ARCH_SIZE//2, ARCH_SIZE//2)
    for reg in ControlRegister:
        _name = RegisterName(reg.name)
        regs[_name] = RegisterInfo(_name, ARCH_SIZE)
        system_regs.append(_name)

    stack_pointer = 'B15'
    link_reg = 'B3'

    disasm = Disassembler(isa=ISA.C674X)

    def get_instruction_text_with_context(self, data: bytes, addr: int, context: Any) -> Optional[tuple[list[InstructionTextToken], int]]:
        if len(data) == self.max_instr_length or context is None:
            instructions = self.disasm.disasm(data, addr)
        else:
            end_addr = addr + len(data)
            fp_addr = end_addr - (end_addr % FP_SIZE)
            num_words = (len(data) + HW_SIZE) // ARCH_SIZE
            if fp_addr in context:
                extended_data = data + b'\x00' * (FP_SIZE  - (end_addr % FP_SIZE) - ARCH_SIZE) + context[fp_addr]
            else:
                extended_data = data + b'\x00' * (FP_SIZE  - (end_addr % FP_SIZE))
            instructions = self.disasm.disasm(extended_data, addr, num_words)

        #TODO: add SPLOOP iteration index for SPKERNEL[R] disassembly

        tokens = []
        offset = 0
        parallel = False
        for instruction in instructions:
            tokens.extend(gen_tokens(instruction, offset, 
                    parallel and not instruction.is_fp_header()))
            offset += instruction.size
            # separate execution packets
            if not instruction.is_fp_header():
                parallel = instruction.parallel
                if not parallel: break
        if parallel:
            # stopped in the middle of EP, visualize as parallel fallthrough
            tokens.extend(gen_parallel_fallthrough(offset))
        return tokens, offset
    
    def get_instruction_text(self, data: bytes, addr: int) -> Optional[tuple[list[InstructionTextToken], int]]:
        # Cannot reliably provide tokens without context, abort instead.
        return None

    def get_instruction_low_level_il(self, data, addr, il):
        # data, _ = self.__header_workaround(data, addr)
        instruction = self.disasm.decode(data, addr)
        il.append(il.unimplemented())
        return instruction.size
    
    def __header_workaround(self, data, addr):
        words = len(data)//ARCH_SIZE
        if (len(data) != self.max_instr_length 
                and (addr + len(data)) % FP_SIZE):
            fill_words = 8 - ((words + (addr//ARCH_SIZE)) % 8) 
            return data[:-ARCH_SIZE] + b'\x00'*(ARCH_SIZE*fill_words) + data[-ARCH_SIZE:], words - 1
        return data, words