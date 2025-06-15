from binaryninja.lowlevelil import LowLevelILFunction, ExpressionIndex
from binaryninja import log_warn

from .constants import ARCH_SIZE, HALF_SIZE
from .instruction import Disassembler
from .disassembler.types import Instruction, Operand, ImmediateOperand, \
        RegisterOperand


#TODO: lift conditional execution

## Helpers

def to_il(operand:Operand, il:LowLevelILFunction) -> ExpressionIndex:
    match operand:
        case ImmediateOperand(value):
            return il.const(HALF_SIZE, value)
        case RegisterOperand(register):
            return il.reg(ARCH_SIZE, str(register))
        case _:
            raise NotImplementedError(f'lifting of {type(operand)}')


## Simple instruction lifting (without delays)

def lift_add(instr:Instruction, il:LowLevelILFunction):
    #TODO: handle all input variants and determine sizes based on ops
    src1 = to_il(instr.operands[0], il)
    src2 = to_il(instr.operands[1], il)
    dst = str(instr.operands[2])
    il.append(il.set_reg(ARCH_SIZE, dst, il.add(
        ARCH_SIZE, src1, src2
    )))

def lift_addk(instr:Instruction, il:LowLevelILFunction):
    assert isinstance(instr.operands[0], ImmediateOperand)
    imm = instr.operands[0].value
    reg = str(instr.operands[1])
    il.append(il.set_reg(ARCH_SIZE, reg, il.add(
        ARCH_SIZE, il.const(HALF_SIZE, imm), il.reg(ARCH_SIZE, reg)
    )))

def lift_mvk(instr: Instruction, il: LowLevelILFunction):
    assert isinstance(instr.operands[0], ImmediateOperand)
    imm = instr.operands[0].value
    reg = str(instr.operands[1])
    value = il.sign_extend(ARCH_SIZE, il.const(HALF_SIZE, imm))
    il.append(il.set_reg(ARCH_SIZE, reg, value))

def lift_mvkh(instr: Instruction, il: LowLevelILFunction):
    assert isinstance(instr.operands[0], ImmediateOperand)
    imm = instr.operands[0].value
    reg = str(instr.operands[1])
    il.append(il.set_reg(ARCH_SIZE, reg+"H", il.const(HALF_SIZE, imm)))

def lift_nop(instr: Instruction, il: LowLevelILFunction):
    il.append(il.nop())


## Pseudo-instruction lifting

def lift_mv(instr: Instruction, il: LowLevelILFunction):
    il.append(il.set_reg(ARCH_SIZE, str(instr.operands[1]),
            il.reg(ARCH_SIZE, str(instr.operands[0]))))


## Delayed instruction lifting

def lift_branch(instr: Instruction, il: LowLevelILFunction):
    il.append(il.call(il.reg(ARCH_SIZE, str(instr.operands[0]))))
    return


HANDLERS_BY_MNEMONIC = {
    'add': lift_add,
    'addk': lift_addk,
    'b': lift_branch,
    'mvk': lift_mvk,
    'mvkl': lift_mvk,
    'mvkh': lift_mvkh,
    'mvklh': lift_mvkh,
    'nop': lift_nop,

    # Pseudo-instruction
    'mv': lift_mv
}

INSTRUCTION_DELAY = {
    'b': 5
}

def get_delay_consumption(instr:Instruction):
    delay_slots = 1
    if instr.opcode == 'nop':
        assert isinstance(instr.operands[0], ImmediateOperand)
        delay_slots = instr.operands[0].value
    elif instr.opcode == 'idle':
        # in theory unlimited, but binja limits delay to 255
        delay_slots = 256
    if instr.parallel:
        delay_slots -= 1
    return delay_slots


def lift_simple(instr:Instruction, il:LowLevelILFunction):
    if instr.opcode not in HANDLERS_BY_MNEMONIC:
        il.append(il.unimplemented())
    else:
        HANDLERS_BY_MNEMONIC[instr.opcode](instr, il)
    return ARCH_SIZE

def lift_delayed(instr:Instruction, disasm:Disassembler, data, addr, il:LowLevelILFunction):
    delay_slots = INSTRUCTION_DELAY[instr.opcode]
    offset = ARCH_SIZE
    if instr.parallel:
        # current fetch packet needs to be finished first
        delay_slots += 1
    while delay_slots > 0 and len(data) > offset:
        current_instr = disasm.decode(data[offset:], addr+offset)
        if current_instr.instr is None:
            log_warn('Lifting of delayed instruction interrupted by invalid instruction')
            return None # could not disassemble, abort lifting
        offset += current_instr.size
        
        il.set_current_address(il.current_address + ARCH_SIZE)
        delay_slots -= get_delay_consumption(current_instr.instr)
        if current_instr.mnemonic in INSTRUCTION_DELAY:
            il.append(il.unimplemented())
        else:
            lift_simple(current_instr.instr, il)
    il.set_current_address(addr)
    HANDLERS_BY_MNEMONIC[instr.opcode](instr, il)
    return offset

def lift_il(disasm:Disassembler, data, addr, il: LowLevelILFunction):
    instr = disasm.decode(data, addr)
    if instr.instr is None:
        return None # could not disassemble, do not lift
    
    if instr.mnemonic in INSTRUCTION_DELAY:
        return lift_delayed(instr.instr, disasm, data, addr, il)

    return lift_simple(instr.instr, il)
