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

from binaryninja.architecture import Architecture, RegisterInfo, RegisterName, BasicBlockAnalysisContext, FunctionLifterContext, InstructionTextToken
from binaryninja.enums import ImplicitRegisterExtend
from binaryninja.function import Function
from binaryninja.log import log_warn, log_error
from binaryninja.lowlevelil import LowLevelILFunction

from typing import Any, Optional

from .disassembler.types import Register, ControlRegister, ISA
from .analysis import analyze_basic_blocks
from .instruction import Disassembler, gen_tokens, gen_parallel_fallthrough
from .constants import *
from .lifting import lift_instructions, lift_function
from .util import get_delay_consumption, is_branch


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

    @Architecture.can_assemble.getter
    def can_assemble(self) -> bool:
        return False
    
    def is_never_branch_patch_available(self, data: bytes, addr: int = 0) -> bool:
        instr = self.disasm.decode_single(data, addr)
        return is_branch(instr)
    
    def is_always_branch_patch_available(self, data: bytes, addr: int = 0) -> bool:
        if len(data) != ARCH_SIZE: 
            return False # cannot patch compact branch predicates
        instr = self.disasm.decode_single(data, addr)
        return is_branch(instr) and instr.condition.register is not None

    def is_invert_branch_patch_available(self, data: bytes, addr: int = 0) -> bool:
        if len(data) != ARCH_SIZE: 
            return False # cannot patch compact branch predicates
        instr = self.disasm.decode_single(data, addr)
        return is_branch(instr) and instr.condition.register is not None
    
    def is_skip_and_return_zero_patch_available(self, data: bytes, addr: int = 0) -> bool:
        # Requires single instruction disassembly and call detection.
        return False
    
    def is_skip_and_return_value_patch_available(self, data: bytes, addr: int = 0) -> bool:
        # Requires single instruction disassembly and call detection.
        return False
    
    def convert_to_nop(self, data: bytes, addr: int = 0) -> Optional[bytes]:
        if len(data) > ARCH_SIZE:
            # not supported because not header-aware
            return None
        instr = self.disasm.decode_single(data, addr)
        # limit to max nop delay for IDLE
        delay = min(8, get_delay_consumption(instr) - 1)
        if instr.opcode.startswith('ld') and addr % 0x20 != 0x1c:
            log_warn(f'NOPing load instruction is unaware of protected loads @{addr:08x}')
        if len(data) == 2:
            if addr & 0x1f == 0x1e:
                log_error(f'Failed to convert invalid instruction @{addr:08x} to NOP.')
                return None
            delay = min(7, delay) # only 3 bits for compact NOP
            return bytes([0x6e, (delay << 5) | 0x0c])
        elif len(data) == 4:
            if addr & 0x2:
                log_error(f'Failed to convert invalid instruction @{addr:08x} to NOP.')
                return None
            # preserve headers
            if data[-1] & 0xf0 == 0xe0: return data
            return bytes([data[0] & 1, 0 | ((delay & 7) << 5), 0 | ((delay & 8) >> 3), 0])
        return None
    
    def never_branch(self, data: bytes, addr: int = 0) -> Optional[bytes]:
        return self.convert_to_nop(data, addr)
    
    def always_branch(self, data: bytes, addr: int = 0) -> Optional[bytes]:
        if len(data) != ARCH_SIZE or addr & 2:
            return None
        # NOTE: BDEC and BPOS are not replaced with B
        return data[:-1] + bytes([data[-1] & 0xf])
    
    def invert_branch(self, data: bytes, addr: int = 0) -> Optional[bytes]:
        if len(data) != ARCH_SIZE or addr & 2:
            return None
        # NOTE: BDEC and BPOS are not inverted
        return data[:-1] + bytes([data[-1] ^ 0x10])

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
            header = context.headers.get(fp_addr, None)
            # SPKERNEL(R) is always first in EP
            sploop_ii = context.sploop_ii.get(addr, 0)
            instructions = self.disasm.disasm(data, addr, end=end_addr, header=header, sploop_ii=sploop_ii)

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

    def lift_function(self, func: LowLevelILFunction,
                      context: FunctionLifterContext) -> bool:
        return lift_function(self, func, context)
