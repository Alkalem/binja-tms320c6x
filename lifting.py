from binaryninja.lowlevelil import LowLevelILFunction, ExpressionIndex
from binaryninja import log_warn

from .constants import ARCH_SIZE, HALF_SIZE
from .instruction import Instruction, Operand, IntegerOperand, RegisterOperand


#TODO: lift conditional execution

def to_il(operand:Operand, il:LowLevelILFunction) -> ExpressionIndex:
    if isinstance(operand, IntegerOperand):
        return il.const(HALF_SIZE, operand.get_value())
    elif isinstance(operand, RegisterOperand):
        return il.reg(ARCH_SIZE, operand.get_value())
    else:
        raise NotImplementedError(f'lifting of {type(operand)}')

def lift_add(instr:Instruction, il:LowLevelILFunction):
    #TODO: handle all input variants and determine sizes based on ops
    src1 = to_il(instr.ops[0], il)
    src2 = to_il(instr.ops[1], il)
    dst = instr.ops[2].get_value()
    il.append(il.set_reg(ARCH_SIZE, dst, il.add(
        ARCH_SIZE, src1, src2
    )))

def lift_addk(instr:Instruction, il:LowLevelILFunction):
    imm = instr.ops[0].get_value()
    reg = instr.ops[1].get_value()
    il.append(il.set_reg(ARCH_SIZE, reg, il.add(
        ARCH_SIZE, il.const(HALF_SIZE, imm), il.reg(ARCH_SIZE, reg)
    )))

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


def lift_branch(instr: Instruction, il: LowLevelILFunction):
    il.append(il.call(il.reg(ARCH_SIZE, instr.ops[0].get_value())))
    return

def lift_mv(instr: Instruction, il: LowLevelILFunction):
    il.append(il.set_reg(ARCH_SIZE, instr.ops[1].get_value(),
            il.reg(ARCH_SIZE, instr.ops[0].get_value())))


HANDLERS_BY_MNEMONIC = {
    'ADD': lift_add,
    'ADDK': lift_addk,
    'B': lift_branch,
    'MVK': lift_mvk,
    'MVKL': lift_mvk,
    'MVKH': lift_mvkh,
    'MVKLH': lift_mvkh,
    'NOP': lift_nop,

    # Capstone pseudo-instruction
    'MV': lift_mv
}


def lift_il(instr: Instruction, il: LowLevelILFunction):
    if instr.mnemonic == 'invalid':
        return None # could not disassemble, do not lift
    
    if instr.mnemonic not in HANDLERS_BY_MNEMONIC:
        il.append(il.unimplemented())
    else:
        HANDLERS_BY_MNEMONIC[instr.mnemonic](instr, il)
    return instr.size
