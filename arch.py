from binaryninja.architecture import Architecture, RegisterInfo, \
    RegisterName
from binaryninja import CallingConvention

from .disassembler.types import Register, ControlRegister, ISA
from .instruction import Disassembler, gen_tokens
from .constants import *
from .lifting import lift_il


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
        return lift_il(self.disasm, data, addr, il)

class TMS320C67x(TMS320C6xBaseArch):
    name = 'TMS320C67x+'

    regs = dict()
    system_regs = list()
    for reg in Register:
        if reg & 16: continue # skip high registers
        _name = RegisterName(reg.name)
        regs[_name] = RegisterInfo(_name, ARCH_SIZE)
        _name = RegisterName(reg.name+'H')
        regs[_name] = RegisterInfo(_name, ARCH_SIZE//2, ARCH_SIZE//2)
    for reg in ControlRegister:
        if reg.isa not in ISA.C67XP: continue
        _name = RegisterName(reg.name)
        regs[_name] = RegisterInfo(_name, ARCH_SIZE)
        system_regs.append(_name)

    stack_pointer = 'B15'
    
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
        _name = RegisterName(reg.name+'H')
        regs[_name] = RegisterInfo(_name, ARCH_SIZE//2, ARCH_SIZE//2)
    for reg in ControlRegister:
        _name = RegisterName(reg.name)
        regs[_name] = RegisterInfo(_name, ARCH_SIZE)
        system_regs.append(_name)

    stack_pointer = 'B15'

    disasm = Disassembler(isa=ISA.C674X)

    def get_instruction_text(self, data, addr):
        instructions = self.disasm.disasm(data, addr)
        tokens = []
        offset = 0
        parallel = False
        sploop = False
        for instruction in instructions:
            tokens.extend(gen_tokens(instruction, offset, 
                    parallel and not instruction.is_fp_header()))
            offset += instruction.size
            if 'sploop' in instruction.opcode:
                sploop = True
            elif 'spkernel' in instruction.opcode:
                sploop = False
            # separate execution packets, unless in sploop body
            if (not instruction.parallel 
                    and not instruction.is_fp_header()
                    and not sploop): break
            if not instruction.is_fp_header():
                parallel = instruction.parallel
        return tokens, offset

