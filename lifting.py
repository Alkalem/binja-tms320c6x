from binaryninja.lowlevelil import LowLevelILFunction, ExpressionIndex
from binaryninja import log_warn

from .constants import ARCH_SIZE, HALF_SIZE
from .instruction import Disassembler, Instruction, Operand, IntegerOperand, RegisterOperand


#TODO: lift conditional execution

## Helpers

def to_il(operand:Operand, il:LowLevelILFunction) -> ExpressionIndex:
    if isinstance(operand, IntegerOperand):
        return il.const(HALF_SIZE, operand.get_value())
    elif isinstance(operand, RegisterOperand):
        return il.reg(ARCH_SIZE, operand.get_value())
    else:
        raise NotImplementedError(f'lifting of {type(operand)}')


## Simple instruction lifting (without delays)

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


## Pseudo-instruction lifting

def lift_mv(instr: Instruction, il: LowLevelILFunction):
    il.append(il.set_reg(ARCH_SIZE, instr.ops[1].get_value(),
            il.reg(ARCH_SIZE, instr.ops[0].get_value())))


## Delayed instruction lifting

def lift_branch(instr: Instruction, il: LowLevelILFunction):
    il.append(il.call(il.reg(ARCH_SIZE, instr.ops[0].get_value())))
    return


HANDLERS_BY_MNEMONIC = {
    'ADD': lift_add,
    'ADDK': lift_addk,
    'B': lift_branch,
    'MVK': lift_mvk,
    'MVKL': lift_mvk,
    'MVKH': lift_mvkh,
    'MVKLH': lift_mvkh,
    'NOP': lift_nop,

    # Pseudo-instruction
    'MV': lift_mv
}

INSTRUCTION_DELAY = {
    'B': 5
}

def get_delay_consumption(instr:Instruction):
    delay_slots = 1
    if instr.mnemonic == 'NOP':
        delay_slots = instr.ops[0].get_value()
    elif instr.mnemonic == 'IDLE':
        # in theory unlimited, but binja limits delay to 255
        delay_slots = 256
    if instr.parallel:
        delay_slots -= 1
    return delay_slots


def lift_simple(instr:Instruction, il:LowLevelILFunction):
    if instr.mnemonic not in HANDLERS_BY_MNEMONIC:
        il.append(il.unimplemented())
    else:
        HANDLERS_BY_MNEMONIC[instr.mnemonic](instr, il)
    return instr.size

def lift_delayed(instr:Instruction, disasm:Disassembler, data, addr, il:LowLevelILFunction):
    delay_slots = INSTRUCTION_DELAY[instr.mnemonic]
    offset = instr.size
    if instr.parallel:
        # current fetch packet needs to be finished first
        delay_slots += 1
    while delay_slots > 0 and len(data) > offset:
        current_instr = disasm.decode(data[offset:], addr+offset)
        if instr.mnemonic == 'invalid':
            log_warn('Lifting of delayed instruction interrupted by invalid instruction')
            return None # could not disassemble, abort lifting
        offset += current_instr.size
        
        il.set_current_address(il.current_address + ARCH_SIZE)
        delay_slots -= get_delay_consumption(current_instr)
        if current_instr.mnemonic in INSTRUCTION_DELAY:
            il.append(il.unimplemented())
        else:
            lift_simple(current_instr, il)
    il.set_current_address(addr)
    HANDLERS_BY_MNEMONIC[instr.mnemonic](instr, il)
    return offset

def lift_il(disasm:Disassembler, data, addr, il: LowLevelILFunction):
    instr = disasm.decode(data, addr)
    if instr.mnemonic == 'invalid':
        return None # could not disassemble, do not lift
    
    if instr.mnemonic in INSTRUCTION_DELAY:
        return lift_delayed(instr, disasm, data, addr, il)

    return lift_simple(instr, il)
