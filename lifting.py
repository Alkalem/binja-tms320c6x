from binaryninja.lowlevelil import LowLevelILFunction

from .instruction import Instruction


def lift_nop(instr: Instruction, il: LowLevelILFunction):
    il.append(il.nop())


HANDLERS_BY_MNEMONIC = {
    "NOP": lift_nop
}


def lift_il(instr: Instruction, il: LowLevelILFunction):
    if instr.mnemonic == 'invalid':
        return None # could not disassemble, do not lift
    
    if instr.mnemonic not in HANDLERS_BY_MNEMONIC:
        return None

    HANDLERS_BY_MNEMONIC[instr.mnemonic](instr, il)
    return instr.size
