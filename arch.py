from binaryninja.architecture import Architecture, BasicBlockAnalysisContext, RegisterInfo
from binaryninja.callingconvention import CallingConvention
from binaryninja.function import Function

from .analysis import analyze_basic_blocks
from .constants import *
from .instruction import Disassembler, gen_tokens, RegisterOperand
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
        if instruction is not None:
            tokens = gen_tokens(instruction)
        else:
            tokens = []
        return tokens, ARCH_SIZE
    
    def get_instruction_low_level_il(self, data, addr, il):
        return lift_il(self.disasm, data, addr, il)
    
    def analyze_basic_blocks(self, func: Function, 
            context: BasicBlockAnalysisContext) -> None:
        analyze_basic_blocks(self, func, context)
    

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

