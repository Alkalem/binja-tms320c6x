from binaryninja.lowlevelil import LowLevelILFunction, ExpressionIndex, \
        LLIL_TEMP, LLIL_GET_TEMP_REG_INDEX
from binaryninja import log_warn

from typing import List

from .constants import ARCH_SIZE, HW_SIZE, DW_SIZE, INSTRUCTION_DELAY
from .instruction import Disassembler
from .util import get_delay_consumption
from .disassembler.types import Instruction, Operand, ImmediateOperand, \
        RegisterOperand, MemoryOperand, Register, AddressingMode


#TODO: lift conditional execution

## Helpers

class LiftingContext:
    def __init__(self) -> None:
        self.max_temp_reg:int = 0
        self.free_temp_regs:List[int] = list()

def to_il(operand:Operand, il:LowLevelILFunction) -> ExpressionIndex:
    match operand:
        case ImmediateOperand(value):
            return il.const(HW_SIZE, value)
        case RegisterOperand(register):
            return il.reg(ARCH_SIZE, str(register))
        case MemoryOperand(mode, base, offset):
            base_il = il.reg(ARCH_SIZE, str(base))
            if isinstance(offset, Register):
                offset_il = il.shift_left(ARCH_SIZE, 
                        il.reg(ARCH_SIZE, str(offset)),
                        il.const(ARCH_SIZE, 2))
            else:
                offset_il = il.const(ARCH_SIZE, offset)
            match mode:
                case AddressingMode.NEG_OFFSET:
                    offset_il = il.neg_expr(ARCH_SIZE, offset_il)
                case AddressingMode.PREDECREMENT:
                    il.append(il.set_reg(ARCH_SIZE, str(base), 
                            il.sub(ARCH_SIZE, base_il, offset_il)))
                case AddressingMode.PREINCREMENT:
                    il.append(il.set_reg(ARCH_SIZE, str(base), 
                            il.add(ARCH_SIZE, base_il, offset_il)))
            match mode:
                case (AddressingMode.POS_OFFSET 
                        | AddressingMode.NEG_OFFSET):
                    return il.add(ARCH_SIZE, base_il, offset_il)
                case _:
                    return base_il
        case _:
            raise NotImplementedError(f'lifting of {type(operand)}')

def post_instr(operand:Operand, il:LowLevelILFunction):
    match operand:
        case MemoryOperand(mode, base, offset):
            base_il = il.reg(ARCH_SIZE, str(base))
            if isinstance(offset, Register):
                offset_il = il.shift_left(ARCH_SIZE, 
                        il.reg(ARCH_SIZE, str(offset)),
                        il.const(ARCH_SIZE, 2))
            else:
                offset_il = il.const(ARCH_SIZE, offset)
            match mode:
                case AddressingMode.POSTDECREMENT:
                    il.append(il.set_reg(ARCH_SIZE, str(base), 
                            il.sub(ARCH_SIZE, base_il, offset_il)))
                case AddressingMode.POSTINCREMENT:
                    il.append(il.set_reg(ARCH_SIZE, str(base), 
                            il.add(ARCH_SIZE, base_il, offset_il)))


## Simple instruction lifting (without delays)

def _lift_bin_op(instr:Instruction, il:LowLevelILFunction, op):
    #TODO: handle all input variants and determine sizes based on ops
    src1 = to_il(instr.operands[0], il)
    src2 = to_il(instr.operands[1], il)
    dst = str(instr.operands[2])
    il.append(il.set_reg(ARCH_SIZE, dst, op(
        ARCH_SIZE, src1, src2
    )))

def lift_add(instr:Instruction, il:LowLevelILFunction, ctx:LiftingContext):
    _lift_bin_op(instr, il, il.add)

def lift_addk(instr:Instruction, il:LowLevelILFunction, ctx:LiftingContext):
    assert isinstance(instr.operands[0], ImmediateOperand)
    imm = instr.operands[0].value
    reg = str(instr.operands[1])
    il.append(il.set_reg(ARCH_SIZE, reg, il.add(
        ARCH_SIZE, il.const(HW_SIZE, imm), il.reg(ARCH_SIZE, reg)
    )))

def lift_cmpeq(instr:Instruction, il:LowLevelILFunction, ctx:LiftingContext):
    src1 = to_il(instr.operands[0], il)
    src2 = to_il(instr.operands[1], il)
    il.append(il.set_reg(ARCH_SIZE, str(instr.operands[2]), 
            il.compare_equal(ARCH_SIZE, src1, src2)))
    
def lift_cmplt(instr:Instruction, il:LowLevelILFunction, ctx:LiftingContext):
    src1 = to_il(instr.operands[0], il)
    src2 = to_il(instr.operands[1], il)
    il.append(il.set_reg(ARCH_SIZE, str(instr.operands[2]), 
            il.compare_signed_less_than(ARCH_SIZE, src1, src2)))

def lift_mvk(instr: Instruction, il: LowLevelILFunction, ctx:LiftingContext):
    assert isinstance(instr.operands[0], ImmediateOperand)
    imm = instr.operands[0].value
    reg = str(instr.operands[1])
    value = il.sign_extend(ARCH_SIZE, il.const(HW_SIZE, imm))
    il.append(il.set_reg(ARCH_SIZE, reg, value))

def lift_mvkh(instr: Instruction, il: LowLevelILFunction, ctx:LiftingContext):
    assert isinstance(instr.operands[0], ImmediateOperand)
    imm = instr.operands[0].value
    reg = str(instr.operands[1])
    il.append(il.set_reg(HW_SIZE, reg+"H", il.const(HW_SIZE, imm)))

def lift_nop(instr: Instruction, il: LowLevelILFunction, ctx:LiftingContext):
    il.append(il.nop())

def lift_sub(instr: Instruction, il: LowLevelILFunction, ctx:LiftingContext):
    _lift_bin_op(instr, il, il.sub)

## Pseudo-instruction lifting

def lift_mv(instr: Instruction, il: LowLevelILFunction, ctx:LiftingContext):
    il.append(il.set_reg(ARCH_SIZE, str(instr.operands[1]),
            il.reg(ARCH_SIZE, str(instr.operands[0]))))

## Temporary IL registers

def alloc_temp(ctx:LiftingContext) -> int:
    if len(ctx.free_temp_regs) == 0:
        reg = ctx.max_temp_reg
        ctx.max_temp_reg += 1
        return reg
    else:
        return ctx.free_temp_regs.pop()

def store_temp(reg_id:int, value, il):
    tmp = LLIL_TEMP(reg_id)
    il.append(il.set_reg(ARCH_SIZE, tmp, value))

def get_temp(reg_id, il):
    tmp = LLIL_TEMP(reg_id)
    return il.reg(ARCH_SIZE, tmp)

def free_temp(ctx:LiftingContext, tmp):
    ctx.free_temp_regs.append(LLIL_GET_TEMP_REG_INDEX(tmp))


## Delayed instruction lifting


def lift_branch(instr:Instruction, il:LowLevelILFunction, ctx:LiftingContext):
    target = alloc_temp(ctx)
    store_temp(target, il.reg(ARCH_SIZE, str(instr.operands[0])), il)

    def branch(il:LowLevelILFunction):
        il.set_current_address(instr.address)
        addr = get_temp(target, il)
        if str(instr.operands[0]) == 'B3':
            il.append(il.ret(addr))
        else:
            il.append(il.call(addr))
        free_temp(ctx, target)
    return ((5, branch),)

def lift_ldb(instr:Instruction, il:LowLevelILFunction, ctx:LiftingContext):
    src = alloc_temp(ctx)
    store_temp(src, to_il(instr.operands[0], il), il)
    post_instr(instr.operands[0], il)

    def load(il:LowLevelILFunction):
        il.set_current_address(instr.address)
        value = il.load(1, get_temp(src, il))
        il.append(il.set_reg(ARCH_SIZE, str(instr.operands[1]), value))
        free_temp(ctx, src)
    return ((4, load),)

def lift_ldw(instr:Instruction, il:LowLevelILFunction, ctx:LiftingContext):
    src = alloc_temp(ctx)
    store_temp(src, to_il(instr.operands[0], il), il)
    post_instr(instr.operands[0], il)

    def load(il:LowLevelILFunction):
        il.set_current_address(instr.address)
        value = il.load(ARCH_SIZE, get_temp(src, il))
        il.append(il.set_reg(ARCH_SIZE, str(instr.operands[1]), value))
        free_temp(ctx, src)
    return ((4, load),)

def lift_mpyi(instr:Instruction, il:LowLevelILFunction, ctx:LiftingContext):
    src1 = alloc_temp(ctx)
    store_temp(src1, to_il(instr.operands[0], il), il)
    src2 = alloc_temp(ctx)
    store_temp(src2, to_il(instr.operands[1], il), il)
    
    def result(il):
        il.set_current_address(instr.address)
        il.append(il.set_reg(ARCH_SIZE, str(instr.operands[2]), 
            il.mult(ARCH_SIZE, get_temp(src1, il), get_temp(src2, il))))
        free_temp(ctx, src1)
        free_temp(ctx, src2)
    return ((8, result),)

def lift_stb(instr:Instruction, il:LowLevelILFunction, ctx:LiftingContext):
    value = alloc_temp(ctx)
    store_temp(value, to_il(instr.operands[0], il), il)
    dest = alloc_temp(ctx)
    store_temp(dest, to_il(instr.operands[1], il), il)
    post_instr(instr.operands[1], il)
    
    def store(il:LowLevelILFunction):
        il.set_current_address(instr.address)
        il.append(il.store(1, get_temp(dest, il), get_temp(value, il)))
        free_temp(ctx, value)
        free_temp(ctx, dest)
    return ((4, store),)

def lift_stw(instr:Instruction, il:LowLevelILFunction, ctx:LiftingContext):
    value = alloc_temp(ctx)
    store_temp(value, to_il(instr.operands[0], il), il)
    dest = alloc_temp(ctx)
    store_temp(dest, to_il(instr.operands[1], il), il)
    post_instr(instr.operands[1], il)
    
    def store(il:LowLevelILFunction):
        il.set_current_address(instr.address)
        il.append(il.store(ARCH_SIZE, get_temp(dest, il), get_temp(value, il)))
        free_temp(ctx, value)
        free_temp(ctx, dest)
    return ((4, store),)


HANDLERS_BY_MNEMONIC = {
    'add': lift_add,
    'addk': lift_addk,
    'b': lift_branch,
    'cmpeq': lift_cmpeq,
    'cmplt': lift_cmplt,
    'ldb': lift_ldb,
    'ldw': lift_ldw,
    'mpyi': lift_mpyi,
    'mvk': lift_mvk,
    'mvkl': lift_mvk,
    'mvkh': lift_mvkh,
    'mvklh': lift_mvkh,
    'nop': lift_nop,
    'stb': lift_stb,
    'stw': lift_stw,
    'sub': lift_sub,

    # Pseudo-instruction
    'mv': lift_mv
}


def lift_simple(instr:Instruction, il:LowLevelILFunction, ctx:LiftingContext):
    if instr.opcode not in HANDLERS_BY_MNEMONIC:
        il.append(il.unimplemented())
    else:
        HANDLERS_BY_MNEMONIC[instr.opcode](instr, il, ctx)
    return ARCH_SIZE

def lift_simple_packet(packet:List[Instruction], il:LowLevelILFunction):
    ctx = LiftingContext()
    lifted_bytes = 0
    for instr in packet:
        if instr.is_invalid(): 
            # could not disassemble, do not lift
            continue 
        il.set_current_address(instr.address)
        lifted_bytes += lift_simple(instr, il, ctx)
    return lifted_bytes

def lift_delayed_packet(packet:List[Instruction], disasm:Disassembler, 
        stream, il:LowLevelILFunction):
    ctx = LiftingContext()
    lifted_bytes = 0
    delay_slots = list()
    while True:
        for instr in packet:
            il.set_current_address(instr.address)
            if instr.is_invalid():
                continue
            if instr.opcode in INSTRUCTION_DELAY:
                new_delay = INSTRUCTION_DELAY[instr.opcode]
                while len(delay_slots) < new_delay+1:
                    delay_slots.append(list())
                for delay, callback in HANDLERS_BY_MNEMONIC[instr.opcode](instr, il, ctx):
                    if instr.opcode == 'b': 
                        # branching is always last action in execution packet
                        delay_slots[delay].append(callback)
                    else:
                        delay_slots[delay].insert(0, callback)
                lifted_bytes += ARCH_SIZE
            else:
                lifted_bytes += lift_simple(instr, il, ctx)
        consumed_slots = max([get_delay_consumption(instr) for instr in packet])
        for _ in range(consumed_slots):
            if len(delay_slots) == 0: break
            slot = delay_slots.pop(0)
            for callback in slot:
                callback(il)
        
        if len(delay_slots) == 0: break
        packet = get_execution_packet(disasm, stream)
        if len(packet) == 0:
            for slot in delay_slots:
                for callback in slot:
                    callback(il)
            break
    return lifted_bytes

def lift_il(disasm:Disassembler, data:bytes, addr:int, il: LowLevelILFunction):
    instruction_stream = gen_instructions(data, addr)
    execution_packet = get_execution_packet(disasm, instruction_stream)
    
    if any([instr.opcode in INSTRUCTION_DELAY for instr in execution_packet]):
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
        


