from binaryninja.lowlevelil import LowLevelILFunction
from binaryninja import log_warn

from .constants import ARCH_SIZE, HALF_SIZE
from .instruction import Instruction


def lift_mvk(instr: Instruction, il: LowLevelILFunction):
    imm = instr.ops[0].get_value()
    reg = instr.ops[1].get_value()
    value = il.sign_extend(ARCH_SIZE, il.const(HALF_SIZE, imm))
    il.append(il.set_reg(ARCH_SIZE, reg, value))

def lift_mvkh(instr: Instruction, il: LowLevelILFunction):
    imm = instr.ops[0].get_value()
    reg = instr.ops[1].get_value()
    il.append(il.set_reg(ARCH_SIZE, reg+"H", il.const(HALF_SIZE, imm)))

def lift_nop(instr: Instruction, il: LowLevelILFunction):
    il.append(il.nop())


HANDLERS_BY_MNEMONIC = {
    'MVK': lift_mvk,
    'MVKL': lift_mvk,
    'MVKH': lift_mvkh,
    'MVKLH': lift_mvkh,
    'NOP': lift_nop
}


def lift_il(instr: Instruction, il: LowLevelILFunction):
    if instr.mnemonic == 'invalid':
        return None # could not disassemble, do not lift
    
    if instr.mnemonic not in HANDLERS_BY_MNEMONIC:
        il.append(il.unimplemented())
    else:
        HANDLERS_BY_MNEMONIC[instr.mnemonic](instr, il)
    return instr.size
