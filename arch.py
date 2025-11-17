from binaryninja.architecture import Architecture
from binaryninja.function import RegisterInfo
from binaryninja import CallingConvention

from .instruction import Disassembler, gen_tokens, RegisterOperand
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
    
    def get_instruction_text(self, data, addr):
        instructions = self.disasm.disasm(data, addr, limit=8)
        tokens = []
        for i, instruction in enumerate(instructions):
            tokens.extend(gen_tokens(instruction, i*ARCH_SIZE))
            # separate execution packets
            if not instruction.parallel: break 
            # stop at fetch packet boundary
            if ((instruction.address+ARCH_SIZE) % (8*ARCH_SIZE) == 0): break
        return tokens, ARCH_SIZE * (i+1)
    
    def get_instruction_low_level_il(self, data, addr, il):
        return lift_il(self.disasm, data, addr, il)

class TMS320C67x(TMS320C6xBaseArch):
    name = 'TMS320C67x'

    regs = {
        name: RegisterInfo(name, ARCH_SIZE)
        for name in REGISTER_NAMES
    } | {
        name+'H': RegisterInfo(name, ARCH_SIZE//2, ARCH_SIZE//2)
        for name in REGISTER_NAMES
    }

    stack_pointer = 'B15'
    
    disasm = Disassembler()


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

