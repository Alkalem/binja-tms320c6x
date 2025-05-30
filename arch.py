from binaryninja.architecture import Architecture
from binaryninja.function import RegisterInfo

from .instruction import Disassembler, gen_tokens, RegisterOperand
from .constants import *
from .lifting import lift_il


class TMS320C67x(Architecture):
    name = 'TMS320C67x'

    address_size = ARCH_SIZE        # 32-bit addresses
    default_int_size = ARCH_SIZE    # 4-byte integers
    instr_alignment = ARCH_SIZE     # fixed 4 byte alignment
    # max_instr_length = ARCH_SIZE  # maximum length
    # Work around: include possible branch delay instructions
    # (see binaryninja-api issue 6868)
    max_instr_length = ARCH_SIZE * (8*(5+1)) 

    regs = {
        name: RegisterInfo(name, ARCH_SIZE)
        for name in REGISTER_NAMES
    } | {
        name+'H': RegisterInfo(name, ARCH_SIZE//2, ARCH_SIZE//2)
        for name in REGISTER_NAMES
    } | {
        name+'L': RegisterInfo(name, 0, ARCH_SIZE//2)
        for name in REGISTER_NAMES
    }

    stack_pointer = 'B15'

    disasm = Disassembler()

    def get_instruction_info(self, data, addr):
        return self.disasm.info(data, addr)
    
    def get_instruction_text(self, data, addr):
        instruction = self.disasm.decode(data, addr)
        tokens = gen_tokens(instruction)
        return tokens, instruction.size
    
    def get_instruction_low_level_il(self, data, addr, il):
        return lift_il(self.disasm, data, addr, il)
