# Copyright 2025-2026 Benedikt Waibel
# 
# This file is part of the binary ninja tms320c6x architecture plugin.
# 
# This plugin is free software: 
# you can redistribute it and/or modify it under the terms of the GNU General
# Public License as published by the Free Software Foundation, either version 3
# of the License, or (at your option) any later version.
# 
# This program is distributed in the hope that it will be useful,but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License along with
# this program. If not, see <http://www.gnu.org/licenses/>.

from binaryninja.lowlevelil import LowLevelILFunction, ExpressionIndex, \
        LLIL_TEMP, LLIL_GET_TEMP_REG_INDEX
from binaryninja.log import log_warn, log_info, log_debug

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
        ARCH_SIZE, il.const(ARCH_SIZE, imm), il.reg(ARCH_SIZE, reg)
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

def store_temp(reg_id, value, il):
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
    value = to_il(instr.operands[0], il)
    dest = to_il(instr.operands[1], il)
    il.append(il.store(1, dest, value))
    post_instr(instr.operands[1], il)

def lift_stw(instr:Instruction, il:LowLevelILFunction, ctx:LiftingContext):
    value = to_il(instr.operands[0], il)
    dest = to_il(instr.operands[1], il)    
    il.append(il.store(ARCH_SIZE, dest, value))
    post_instr(instr.operands[1], il)


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

def lift_il(arch, data:bytes, addr:int, il: LowLevelILFunction):
    disasm:Disassembler = arch.disasm
    instruction_stream = gen_instructions(data, addr)
    execution_packet = get_execution_packet(disasm, instruction_stream)
    
    if any([instr.opcode in INSTRUCTION_DELAY for instr in execution_packet]):
        return lift_delayed_packet(execution_packet, disasm, 
                instruction_stream, il)

    if ALT_LIFTING_START <= execution_packet[0].address <= ALT_LIFTING_END:
        ctx = LiftingContextAlt(arch, il, LiftingSettings(False))
        log_debug(f'Alternative lifting algorithm @{execution_packet[0].address:08x}')
        lift_ep(ctx, execution_packet)
        return sum(map(lambda instr: instr.size, execution_packet))
    return lift_simple_packet(execution_packet, il)

ALT_LIFTING_START = 0x1e18
ALT_LIFTING_END = 0x1e30

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

## WIP: unified parallel and delayed lifting skeleton

from binaryninja.architecture import RegisterName
from binaryninja.lowlevelil import ILRegister

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Optional, Sequence, Iterable

from .disassembler.types import RW, FuncUnitsOperand, ControlRegisterOperand, RegisterPairOperand

@dataclass(frozen=True)
class LiftingSettings:
    header_based: bool
    simplify: bool = False

class TempAllocator:
    def __init__(self, arch) -> None:
        self.arch = arch
        self.max_temp_reg:int = 0
        self.free_temp_regs:List[int] = list()
    
    def alloc(self) -> ILRegister:
        if len(self.free_temp_regs) == 0:
            reg_id = self.max_temp_reg
            self.max_temp_reg += 1
        else:
            reg_id = self.free_temp_regs.pop()
        return ILRegister(self.arch, LLIL_TEMP(reg_id))

    def free(self, tmp):
        self.free_temp_regs.append(LLIL_GET_TEMP_REG_INDEX(tmp))

class TempReadAllocator:
    def __init__(self, il: LowLevelILFunction, temp_alloc: TempAllocator) -> None:
        self.il = il
        self.temp_alloc = temp_alloc
        self.references:dict[RegisterName, int] = dict()
        self.assignments:dict[RegisterName, ILRegister] = dict()

    def alloc(self, reg: RegisterName) -> ILRegister:
        if reg in self.references:
            self.references[reg] += 1
        else:
            self.references[reg] = 1
            temp_reg = self.temp_alloc.alloc()
            self.assignments[reg] = temp_reg
            value = self.il.reg(ARCH_SIZE, reg)
            store_temp(temp_reg, value, self.il)            
        return self.assignments[reg]
    
    def free(self, reg: RegisterName):
        if reg not in self.references: return
        self.references[reg] -= 1
        if self.references[reg] <= 0:
            del self.references[reg]
            self.temp_alloc.free(self.assignments[reg])
            del self.assignments[reg]

class LiftingContextAlt:
    def __init__(self, arch, il: LowLevelILFunction, settings: LiftingSettings) -> None:
        self.arch = arch
        self.il = il
        self.setting = settings
        self.read_queue:LiftingQueue[InputOperand] = LiftingQueue(32)
        self.op_queue:LiftingQueue[Operation] = LiftingQueue(32)
        self.write_queue:LiftingQueue[OutputOperand] = LiftingQueue(32)
        self.temp_alloc = TempAllocator(arch)

def to_il_alt(operand:Operand, il:LowLevelILFunction) -> ExpressionIndex:
    match operand:
        case ImmediateOperand(value):
            return il.const(ARCH_SIZE, value)
        case FuncUnitsOperand(_):
            raise NotImplementedError(f'lifting of functional unit masks')
        case _:
            raise NotImplementedError(f'lifting of {type(operand)}')

def lift_simple_alt(instr:Instruction, il:LowLevelILFunction, ctx:LiftingContext):
    inputs = list()
    output = None
    for operand in instr.operands:
        match operand.access_info.rw:
            case RW.none:
                inputs.append(to_il_alt(operand, il))
            case RW.read:
                inputs.append(operand)
            case RW.read_write:
                assert output is None, 'Only one output operand expected'
                output = operand
            case RW.write:
                assert output is None, 'Only one output operand expected'
                output = operand
    return

def _get_bin_op_cb(il: LowLevelILFunction, op):
    def __lift(src1: ExpressionIndex, src2: ExpressionIndex) -> Sequence[ExpressionIndex]:
        result = op(ARCH_SIZE, src1, src2)
        return (result,)
    return __lift

def get_add_cb(il: LowLevelILFunction):
    return _get_bin_op_cb(il, il.add)

def get_stw_cb(il: LowLevelILFunction):
    def __lift(value: ExpressionIndex, address: ExpressionIndex) -> Sequence[ExpressionIndex]:
        stmt = il.store(ARCH_SIZE, address, value)
        return (stmt,)
    return __lift

def get_unimplemented_cb(il: LowLevelILFunction):
    def __lift(): return (il.unimplemented(),)
    return __lift

_lifting_cb_type = Callable[[ExpressionIndex, ExpressionIndex], Sequence[ExpressionIndex]]
_lifting_gen_type = Callable[[LowLevelILFunction], _lifting_cb_type]
OPCODE_CALLBACKS: dict[str, _lifting_gen_type] = {
    'add': get_add_cb,
    'stw': get_stw_cb,
    'addk': get_add_cb,
}

class LiftingQueue[T]:
    def __init__(self, max_items: int) -> None:
        self.cycle_queues:deque[deque] = deque(maxlen=12)
        self.max_items = max_items

    def enqueue(self, items: list[tuple[int, T]], front=False):
        for delay, value in items:
            while len(self.cycle_queues) <= delay:
                self.cycle_queues.append(deque(maxlen=self.max_items))
            if front:
                self.cycle_queues[delay].appendleft(value)
            else:
                self.cycle_queues[delay].append(value)
    
    def dequeue(self) -> deque[T]:
        if len(self.cycle_queues) == 0:
            return deque()
        else:
            return self.cycle_queues.popleft()

class InputOperand:
    def __init__(self, src: Operand, il: LowLevelILFunction) -> None:
        self.src = src
        self.il = il
        self.handles:dict[RegisterName, ILRegister] = dict()
        self._is_read = False
        self.low = True

    def read(self, allocator: TempReadAllocator):
        if self._is_read: return
        # allocate register reads and store handles
        self._is_read = True
        match self.src:
            case RegisterOperand(reg) | ControlRegisterOperand(reg):
                reg_name = RegisterName(reg.name)
                self.handles[reg_name] = allocator.alloc(reg_name)
            case RegisterPairOperand(high, low):
                if self.low:
                    self._is_read = self.low = False
                    reg_name = RegisterName(low.name)
                else:
                    reg_name = RegisterName(high.name)
                self.handles[reg_name] = allocator.alloc(reg_name)
            case MemoryOperand(_, base, offset, _):
                reg_name = RegisterName(base.name)
                self.handles[reg_name] = allocator.alloc(reg_name)
                if isinstance(offset, Register):
                    reg_name = RegisterName(offset.name)
                    self.handles[reg_name] = allocator.alloc(reg_name)

    def to_expr(self) -> ExpressionIndex:
        il = self.il
        match self.src:
            case ImmediateOperand(value):
                return il.const(ARCH_SIZE, value)
            case RegisterOperand(reg) | ControlRegisterOperand(reg):
                reg = self.handles[RegisterName(reg.name)]
                return il.reg(ARCH_SIZE, reg)
            case RegisterPairOperand(high, low):
                hi = self.handles[RegisterName(high.name)]
                lo = self.handles[RegisterName(low.name)]
                return il.reg_split(ARCH_SIZE, hi, lo)
            case MemoryOperand(mode, base, offset, scaled):
                base = self.handles[RegisterName(base.name)]
                base_il = il.reg(ARCH_SIZE, base)
                if isinstance(offset, Register):
                    offset = self.handles[RegisterName(offset.name)]
                    offset_il = il.reg(ARCH_SIZE, offset)
                else:
                    offset_il = self.il.const(ARCH_SIZE, offset)
                if scaled:
                    offset_il = il.mult(ARCH_SIZE, 
                        offset_il,
                        il.const(ARCH_SIZE, self.src.access_info.size))
                match mode:
                    case (AddressingMode.NEG_OFFSET
                            | AddressingMode.PREDECREMENT
                            | AddressingMode.POSTDECREMENT):
                        offset_il = il.neg_expr(ARCH_SIZE, offset_il)
                self.address = il.add(ARCH_SIZE, base_il, offset_il)
                match mode:
                    case (AddressingMode.POSTDECREMENT 
                            | AddressingMode.POSTINCREMENT):
                        return base_il
                    case _:
                        return self.address
            case FuncUnitsOperand(_):
                raise NotImplementedError(f'lifting of functional unit masks')
            case _:
                raise NotImplementedError(f'lifting of {type(self.src)}')
            
    def get_passthrough(self) -> Optional[ExpressionIndex]:
        if isinstance(self.src, MemoryOperand):
            if self.src.mode in (AddressingMode.NEG_OFFSET, AddressingMode.POS_OFFSET):
                    return None
            return self.address
        return None

class Operation:
    def __init__(self, inputs: list[InputOperand], callback) -> None:
        self.inputs = inputs
        self.callback = callback
        self._outputs = list()
        self._lifted = False
        self._statements: Sequence[ExpressionIndex] = tuple()
        self._output_exprs: Sequence[ExpressionIndex] = tuple()
        self._result = None
        self._stored = False
    
    def _lift(self):
        if self._lifted: return
        input_exprs = (input.to_expr() for input in self.inputs)
        il_exprs = self.callback(*input_exprs)
        output_exprs: list[ExpressionIndex] = list()
        #NOTE: this order of outputs only works if output operand is last
        for input in self.inputs:
            passtrough = input.get_passthrough()
            if passtrough is not None: output_exprs.append(passtrough)
        output_index = len(il_exprs)-len(self._outputs)+len(output_exprs)
        output_exprs.extend(il_exprs[output_index:])
        self._statements = il_exprs[:output_index]
        self._output_exprs = tuple(output_exprs)
        self._lifted = True

    def register_output(self, output: 'OutputOperand'):
        self._outputs.append(output)

    def store(self, allocator: TempAllocator, il: LowLevelILFunction):
        if self._stored: return
        self._lift()
        stored_exprs = list()
        for output_expr in self._output_exprs:
            temp_reg = allocator.alloc()
            store_temp(temp_reg, output_expr, il)
            stored_exprs.append(il.reg(ARCH_SIZE, temp_reg))
        self._output_exprs = stored_exprs
        self._stored = True

    def get_statements(self) -> Iterable[ExpressionIndex]:
        self._lift()
        return self._statements

    def get(self, output: 'OutputOperand') -> ExpressionIndex:
        if output not in self._outputs:
            raise ValueError('requested output needs to be registered')
        self._lift()
        return self._output_exprs[self._outputs.index(output)]
    
    def has_outputs(self) -> bool:
        return len(self._outputs) > 0

class OutputOperand:
    def __init__(self, src:Operand, operation: Operation) -> None:
        self.src = src
        self.operation = operation
        if self._is_writing():
            operation.register_output(self)

    def _is_writing(self) -> bool:
        match self.src:
            case MemoryOperand(mode, _):
                if mode in (AddressingMode.NEG_OFFSET, AddressingMode.POS_OFFSET):
                    return False
        return True

    def write(self, il:LowLevelILFunction):
        if not self._is_writing(): return
        match self.src:
            case RegisterOperand(reg):
                value = self.operation.get(self)
                il.append(il.set_reg(ARCH_SIZE, RegisterName(reg.name), value))
            case MemoryOperand(_, base, _, _):
                value = self.operation.get(self)
                il.append(il.set_reg(ARCH_SIZE, RegisterName(base.name), value))

class LiftInstruction:
    def __init__(self, src: Instruction, il: LowLevelILFunction) -> None:
        self.src = src
        self.inputs: list[InputOperand] = list()
        self._reads = list()
        self._writes = list()
        self.output: OutputOperand | None = None
        self.lift_cycle: int = 0

        if src.opcode not in OPCODE_CALLBACKS:
            self.operation = Operation([], get_unimplemented_cb(il))
            return
        
        for operand in src.operands:
            if isinstance(operand, MemoryOperand):
                # Access info for memory operands documents memory access.
                # Here, register access is relevant, which is RW.
                input = InputOperand(operand, il)
                # Register access is in first cycle.
                # Memory access is in third cycle, but may be lifted without delay.
                self._reads.append((0, input))
                self.inputs.append(input)
            elif operand.access_info.rw in (RW.none, RW.read, RW.read_write):
                input = InputOperand(operand, il)
                if operand.access_info.low_last:
                    self._reads.append((operand.access_info.low_last-1, input))
                if operand.access_info.high_last:
                    self._reads.append((operand.access_info.high_last-1, input))
                self.inputs.append(input)
        self.lift_cycle = max(map(lambda c: c[0], self._reads))
        self.operation = Operation(self.inputs, OPCODE_CALLBACKS[src.opcode](il))
        for operand in src.operands:
            if isinstance(operand, MemoryOperand):
                output = OutputOperand(operand, self.operation)
                # Optional address write is in first cycle.
                self._writes.append((0, output))
            elif operand.access_info.rw in (RW.write, RW.read_write):
                self.output = OutputOperand(operand, self.operation)
                if operand.access_info.low_first:
                    self._writes.append((operand.access_info.low_first-1, self.output))
                if operand.access_info.high_first:
                    self._writes.append((operand.access_info.high_first-1, self.output))

    def is_first(self) -> bool:
        # For load/store in same cycle, load occurs first.
        return self.src.opcode.lower().startswith('ld')

    def get_reads(self) -> list[tuple[int, InputOperand]]:
        return self._reads

    def get_operation(self) -> tuple[int, Operation]:
        return (self.lift_cycle, self.operation)
    
    def get_writes(self) -> list[tuple[int, OutputOperand]]:
        return self._writes

def lift_ep(ctx: LiftingContextAlt, packet: list[Instruction]):
    allocator = TempReadAllocator(ctx.il, ctx.temp_alloc)
    
    # 1. Convert instructions to helper objects for lifting
    lift_packet = [LiftInstruction(instr, ctx.il) for instr in packet]

    # 2. Enqueue parts of the EPs instructions in lifting queues
    for lift_instr in lift_packet:
        ctx.read_queue.enqueue(lift_instr.get_reads())
        ctx.op_queue.enqueue([lift_instr.get_operation()], lift_instr.is_first())
        ctx.write_queue.enqueue(lift_instr.get_writes())
    
    # 3. Translate one cycle of pipeline execution to IL
    for input in ctx.read_queue.dequeue():
        input.read(allocator)
    for operation in ctx.op_queue.dequeue():
        for statement in operation.get_statements():
            ctx.il.append(statement)
        if operation.has_outputs():
            operation.store(ctx.temp_alloc, ctx.il)
    for output in ctx.write_queue.dequeue():
        output.write(ctx.il)

# def prepare_ep_lifting(packet: List[Instruction]) -> LiftPacket:

# def lift_basic_block(block: BasicBlock, func: LowLevelILFunction) -> BlockLiftingResult:
# BlockLiftingResult ~= bool, pending instructions

# def lift_instruction(arch, func: LowLevelILFunction) -> int:
# Lift instruction based until function-based lifting is supported and implemented.
# Should lift entire EPs if possible, may lift until end of block to translate delays correctly.

# def lift_function(arch, func: LowLevelILFunction, context: FunctionLifterContext) -> bool:
# Lift entire function after analysis, by lifting basic blocks, their delayed instructions, and their branches.

