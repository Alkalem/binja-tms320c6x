from binaryninja.lowlevelil import LowLevelILFunction, ExpressionIndex
from binaryninja import log_warn

from typing import List

from .constants import ARCH_SIZE, HALF_SIZE
from .instruction import Disassembler
from .disassembler.types import Instruction, Operand, ImmediateOperand, \
        RegisterOperand, MemoryOperand, Register, AddressingMode


#TODO: lift conditional execution

## Helpers

def to_il(operand:Operand, il:LowLevelILFunction) -> ExpressionIndex:
    match operand:
        case ImmediateOperand(value):
            return il.const(HALF_SIZE, value)
        case RegisterOperand(register):
            return il.reg(ARCH_SIZE, str(register))
        case MemoryOperand(mode, base, offset):
            base_il = il.reg(ARCH_SIZE, str(base))
            if isinstance(offset, Register):
                offset_il = il.reg(ARCH_SIZE, str(offset))
            else:
                offset_il = il.const(ARCH_SIZE, offset)
            match mode:
                case AddressingMode.NEG_OFFSET:
                    offset_il = il.neg_expr(ARCH_SIZE, offset_il)
                case AddressingMode.PREDECREMENT:
                    il.append(il.set_reg(ARCH_SIZE, str(base), 
                            il.sub(ARCH_SIZE, base_il, il.const(ARCH_SIZE, ARCH_SIZE))))
                case AddressingMode.PREINCREMENT:
                    il.append(il.set_reg(ARCH_SIZE, str(base), 
                            il.add(ARCH_SIZE, base_il, il.const(ARCH_SIZE, ARCH_SIZE))))
            return il.add(ARCH_SIZE, base_il, offset_il)
        case _:
            raise NotImplementedError(f'lifting of {type(operand)}')

def post_instr(operand:Operand, il:LowLevelILFunction):
    match operand:
        case MemoryOperand(mode, base, offset):
            base_il = il.reg(ARCH_SIZE, str(base))
            match mode:
                case AddressingMode.POSTDECREMENT:
                    il.append(il.set_reg(ARCH_SIZE, str(base), 
                            il.sub(ARCH_SIZE, base_il, il.const(ARCH_SIZE, ARCH_SIZE))))
                case AddressingMode.POSTINCREMENT:
                    il.append(il.set_reg(ARCH_SIZE, str(base), 
                            il.add(ARCH_SIZE, base_il, il.const(ARCH_SIZE, ARCH_SIZE))))


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
    if str(instr.operands[0]) == 'B3':
        il.append(il.ret(il.reg(ARCH_SIZE, str(instr.operands[0]))))
    else:
        il.append(il.call(il.reg(ARCH_SIZE, str(instr.operands[0]))))

def lift_ldw(instr:Instruction, il:LowLevelILFunction):
    src = to_il(instr.operands[0], il)
    il.append(il.set_reg(ARCH_SIZE, str(instr.operands[1]), il.load(ARCH_SIZE, src)))
    post_instr(instr.operands[0], il)

def lift_stw(instr:Instruction, il:LowLevelILFunction):
    value = to_il(instr.operands[0], il)
    dest = to_il(instr.operands[1], il)
    il.append(il.store(ARCH_SIZE, dest, value))
    post_instr(instr.operands[1], il)


HANDLERS_BY_MNEMONIC = {
    'add': lift_add,
    'addk': lift_addk,
    'b': lift_branch,
    'ldw': lift_ldw,
    'mvk': lift_mvk,
    'mvkl': lift_mvk,
    'mvkh': lift_mvkh,
    'mvklh': lift_mvkh,
    'nop': lift_nop,
    'stw': lift_stw,

    # Pseudo-instruction
    'mv': lift_mv
}

INSTRUCTION_DELAY = {
    'b': 5,
    'ldw': 4,
    'stw': 4
}

def get_delay_consumption(instr:Instruction):
    delay_slots = 1
    if instr.opcode == 'nop':
        assert isinstance(instr.operands[0], ImmediateOperand)
        delay_slots = instr.operands[0].value
    elif instr.opcode == 'idle':
        # in theory unlimited, but binja limits delay to 255
        delay_slots = 255
    return delay_slots


def lift_simple(instr:Instruction, il:LowLevelILFunction):
    if instr.opcode not in HANDLERS_BY_MNEMONIC:
        il.append(il.unimplemented())
    else:
        HANDLERS_BY_MNEMONIC[instr.opcode](instr, il)
    return ARCH_SIZE

def lift_simple_packet(packet:List[Instruction], il:LowLevelILFunction):
    lifted_bytes = 0
    for instr in packet:
        if instr is None: break # could not disassemble, do not lift
        il.set_current_address(instr.address)
        lifted_bytes += lift_simple(instr, il)
    return lifted_bytes

def lift_delayed_packet(packet:List[Instruction], disasm:Disassembler, 
        stream, il:LowLevelILFunction):
    lifted_bytes = 0
    delay_slots = list()
    while True:
        for instr in packet:
            il.set_current_address(instr.address)
            if instr is None:
                log_warn('Lifting of delayed instruction interrupted by invalid instruction')
                return lifted_bytes
            if instr.opcode in INSTRUCTION_DELAY:
                new_delay = INSTRUCTION_DELAY[instr.opcode]
                while len(delay_slots) < new_delay+1:
                    delay_slots.append(list())
                if instr.opcode == 'b': 
                    # branching is always last action in execution packet
                    delay_slots[new_delay].append(instr)
                else:
                    delay_slots[new_delay].insert(0, instr)
                lifted_bytes += ARCH_SIZE
            else:
                lifted_bytes += lift_simple(instr, il)
        consumed_slots = max([get_delay_consumption(instr) for instr in packet])
        for _ in range(consumed_slots):
            if len(delay_slots) == 0: break
            slot = delay_slots.pop(0)
            for instr in slot:
                il.set_current_address(instr.address)
                HANDLERS_BY_MNEMONIC[instr.opcode](instr, il)
        
        if len(delay_slots) == 0: break
        packet = get_execution_packet(disasm, stream)
        if len(packet) == 0:
            log_warn('Lifting of delayed instruction interrupted by empty stream')
            break
    return lifted_bytes

def lift_il(disasm:Disassembler, data:bytes, addr:int, il: LowLevelILFunction):
    instruction_stream = gen_instructions(data, addr)
    execution_packet = get_execution_packet(disasm, instruction_stream)
    
    if any([instr.opcode in INSTRUCTION_DELAY for instr in execution_packet
            if instr is not None]):
        return lift_delayed_packet(execution_packet, disasm, 
                instruction_stream, il)

    return lift_simple_packet(execution_packet, il)


def gen_instructions(data:bytes, addr:int):
    offset = 0
    while len(data) >= offset+ARCH_SIZE:
        yield (addr+offset, data[offset : offset+ARCH_SIZE])
        offset += ARCH_SIZE

def get_execution_packet(disasm:Disassembler, stream) -> List[Instruction]:
    execution_packet = list()
    while True:
        try:
            current_addr, instr_bytes = next(stream)
        except StopIteration:
            break
        instr = disasm.decode(instr_bytes, current_addr)
        execution_packet.append(instr)
        if instr is None: break
        # A fetch packets end at an 8 instruction aligned address,
        # parallel bit chains instructions in same execution packet.
        if not (instr.parallel and ((current_addr+4) % (ARCH_SIZE * 8))): break
    return execution_packet
        


