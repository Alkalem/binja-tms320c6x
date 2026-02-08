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

from __future__ import annotations

from binaryninja.commonil import ILSourceLocation
from binaryninja.function import ArchAndAddr
from binaryninja.lowlevelil import LowLevelILFunction, ExpressionIndex, \
        LLIL_TEMP, LLIL_GET_TEMP_REG_INDEX
from binaryninja.log import log_warn, log_info, log_debug, log_error

from typing import List, TYPE_CHECKING

if TYPE_CHECKING:
    from .arch import TMS320C6xBaseArch
from .arch import FunctionLifterContext
from .constants import ARCH_SIZE, HW_SIZE, DW_SIZE, INSTRUCTION_DELAY, FP_SIZE
from .instruction import Disassembler
from .util import get_delay_consumption, unwrap
from .disassembler.types import Instruction, Operand, ImmediateOperand, \
        RegisterOperand, MemoryOperand, Register, AddressingMode


#TODO: lift conditional execution

## Temporary IL registers

def store_temp(reg_id, value, il: LowLevelILFunction, loc=None):
    tmp = LLIL_TEMP(reg_id)
    il.append(il.set_reg(ARCH_SIZE, tmp, value, loc=loc))

def get_temp(reg_id, il):
    tmp = LLIL_TEMP(reg_id)
    return il.reg(ARCH_SIZE, tmp)


## WIP: unified parallel and delayed lifting skeleton

from binaryninja.architecture import RegisterName
from binaryninja.lowlevelil import ILRegister, LowLevelILReg, LLIL_REG_IS_TEMP
from binaryninja.variable import PossibleValueSet, ValueRange

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Optional, Sequence, Iterable, Generator

from .disassembler.types import RW, FuncUnitsOperand, ControlRegisterOperand, RegisterPairOperand, ControlRegister

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

    def alloc(self, reg: Optional[RegisterName] = None, loc=None) -> ILRegister:
        if reg is None:
            temp_reg = self.temp_alloc.alloc()
            return temp_reg
        if reg in self.references:
            self.references[reg] += 1
        else:
            self.references[reg] = 1
            temp_reg = self.temp_alloc.alloc()
            self.assignments[reg] = temp_reg
            value = self.il.reg(ARCH_SIZE, reg, loc=loc)
            store_temp(temp_reg, value, self.il, loc=loc)
        return self.assignments[reg]
    
    def free(self, reg: RegisterName):
        if reg not in self.references: return
        self.references[reg] -= 1
        if self.references[reg] <= 0:
            del self.references[reg]
            self.temp_alloc.free(self.assignments[reg])
            del self.assignments[reg]

def is_temp_reg(expr: ExpressionIndex, il: LowLevelILFunction) -> bool:
    instr = il.get_expr(expr)
    if instr is None: return False
    if isinstance(instr, LowLevelILReg) and isinstance(instr.src, ILRegister):
        return LLIL_REG_IS_TEMP(instr.src)
    return False

class LiftingContext:
    def __init__(self, arch, il: LowLevelILFunction, settings: LiftingSettings) -> None:
        self.arch = arch
        self.il = il
        self.setting = settings
        self.read_queue:LiftingQueue[InputOperand] = LiftingQueue(32)
        self.op_queue:LiftingQueue[Operation] = LiftingQueue(32)
        self.write_queue:LiftingQueue[OutputOperand] = LiftingQueue(32)
        self.temp_alloc = TempAllocator(arch)
        self.read_alloc = TempReadAllocator(il, self.temp_alloc)


def _get_bin_op_cb(il: LowLevelILFunction, op, loc: ILSourceLocation):
    def __lift(src1: ExpressionIndex, src2: ExpressionIndex) -> Sequence[ExpressionIndex]:
        result = op(ARCH_SIZE, src1, src2, loc=loc)
        return (result,)
    return __lift

def get_add_cb(il: LowLevelILFunction, loc: ILSourceLocation):
    return _get_bin_op_cb(il, il.add, loc)

def get_ldw_cb(il: LowLevelILFunction, loc: ILSourceLocation):
    def __lift(address: ExpressionIndex) -> Sequence[ExpressionIndex]:
        expr = il.load(ARCH_SIZE, address, loc=loc)
        return (expr,)
    return __lift

def get_stw_cb(il: LowLevelILFunction, loc: ILSourceLocation):
    def __lift(value: ExpressionIndex, address: ExpressionIndex) -> Sequence[ExpressionIndex]:
        stmt = il.store(ARCH_SIZE, address, value, loc=loc)
        return (stmt,)
    return __lift

def get_unimplemented_cb(il: LowLevelILFunction, loc: ILSourceLocation):
    def __lift(): return (il.unimplemented(loc=loc),)
    return __lift

_lifting_bin_type = Callable[[ExpressionIndex, ExpressionIndex], Sequence[ExpressionIndex]]
_lifting_un_type = Callable[[ExpressionIndex], Sequence[ExpressionIndex]]
_lifting_gen_type = Callable[[LowLevelILFunction, ILSourceLocation], 
        _lifting_bin_type | _lifting_un_type]
OPCODE_CALLBACKS: dict[str, _lifting_gen_type] = {
    'add': get_add_cb,
    'addk': get_add_cb,
    'b': lambda *_: (lambda inp: (inp,)),
    'ldw': get_ldw_cb,
    'mvk': lambda *_: (lambda inp: (inp,)),
    'mvkh': lambda *_: (lambda inp: (inp,)),
    'nop': lambda il, loc: (lambda *_: (il.nop(loc=loc),)),
    'stw': get_stw_cb,
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
        
    def __len__(self) -> int:
        return len(self.cycle_queues)

class InputOperand:
    def __init__(self, src: Operand, il: LowLevelILFunction, instr: Instruction) -> None:
        self.src = src
        self.loc = _addr2loc(instr.address)
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
                self.handles[reg_name] = allocator.alloc(reg_name, self.loc)
            case RegisterPairOperand(high, low):
                if self.low:
                    self._is_read = self.low = False
                    reg_name = RegisterName(low.name)
                else:
                    reg_name = RegisterName(high.name)
                self.handles[reg_name] = allocator.alloc(reg_name, self.loc)
            case MemoryOperand(_, base, offset, _):
                reg_name = RegisterName(base.name)
                self.handles[reg_name] = allocator.alloc(reg_name, self.loc)
                if isinstance(offset, Register):
                    reg_name = RegisterName(offset.name)
                    self.handles[reg_name] = allocator.alloc(reg_name, self.loc)

    def to_expr(self) -> ExpressionIndex:
        il = self.il
        match self.src:
            case ImmediateOperand(value):
                return il.const(ARCH_SIZE, value, loc=self.loc)
            case RegisterOperand(reg) | ControlRegisterOperand(reg):
                reg = self.handles[RegisterName(reg.name)]
                return il.reg(ARCH_SIZE, reg, loc=self.loc)
            case RegisterPairOperand(high, low):
                hi = self.handles[RegisterName(high.name)]
                lo = self.handles[RegisterName(low.name)]
                return il.reg_split(ARCH_SIZE, hi, lo, loc=self.loc)
            case MemoryOperand(mode, base, offset, scaled):
                base = self.handles[RegisterName(base.name)]
                base_il = il.reg(ARCH_SIZE, base, loc=self.loc)
                if isinstance(offset, Register):
                    offset = self.handles[RegisterName(offset.name)]
                    offset_il = il.reg(ARCH_SIZE, offset, loc=self.loc)
                else:
                    offset_il = self.il.const(ARCH_SIZE, offset, loc=self.loc)
                if scaled:
                    offset_il = il.mult(ARCH_SIZE, 
                        offset_il,
                        il.const(ARCH_SIZE, self.src.access_info.size, loc=self.loc),
                        loc=self.loc)
                match mode:
                    case (AddressingMode.NEG_OFFSET
                            | AddressingMode.PREDECREMENT
                            | AddressingMode.POSTDECREMENT):
                        offset_il = il.neg_expr(ARCH_SIZE, offset_il, loc=self.loc)
                self.address_expr = il.add(ARCH_SIZE, base_il, offset_il, loc=self.loc)
                match mode:
                    case (AddressingMode.POSTDECREMENT 
                            | AddressingMode.POSTINCREMENT):
                        return base_il
                    case _:
                        return self.address_expr
            case FuncUnitsOperand(_):
                raise NotImplementedError(f'lifting of functional unit masks')
            case _:
                raise NotImplementedError(f'lifting of {type(self.src)}')
            
    def get_passthrough(self) -> Optional[ExpressionIndex]:
        if isinstance(self.src, MemoryOperand):
            if self.src.mode in (AddressingMode.NEG_OFFSET, AddressingMode.POS_OFFSET):
                    return None
            return self.address_expr
        return None
    
    def free(self, allocator: TempReadAllocator):
        for reg in self.handles:
            allocator.free(reg)
        self.handles.clear()

class Operation:
    def __init__(self, inputs: list[InputOperand], callback, src: Instruction) -> None:
        self.inputs = inputs
        self.callback = callback
        self.loc = _addr2loc(src.address)
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

    def store(self, allocator: TempReadAllocator, il: LowLevelILFunction):
        if self._stored: return
        self._lift()
        stored_exprs = list()
        for output_expr in self._output_exprs:
            temp_reg = allocator.alloc(loc=self.loc)
            store_temp(temp_reg, output_expr, il, loc=self.loc)
            stored_exprs.append(il.reg(ARCH_SIZE, temp_reg, loc=self.loc))
        self._output_exprs = stored_exprs
        self._stored = True
        for input in self.inputs:
            input.free(allocator)

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
    def __init__(self, src:Operand, operation: Operation, instr: Instruction) -> None:
        self.src = src
        self.operation = operation
        self.instruction = instr
        self.loc = _addr2loc(instr.address)
        self._high = False
        if self._is_writing():
            operation.register_output(self)

    def _is_writing(self) -> bool:
        match self.src:
            case MemoryOperand(mode, _):
                if mode in (AddressingMode.NEG_OFFSET, AddressingMode.POS_OFFSET):
                    return False
        return True

    def write(self, il:LowLevelILFunction, allocator: TempAllocator):
        if not self._is_writing(): return
        value = self.operation.get(self)
        done = True
        match self.src:
            case RegisterOperand(reg):
                if self.instruction.opcode in ('mvkh', 'mvklh'):
                    il.append(il.set_reg(HW_SIZE, RegisterName(reg.name+'H'), value, loc=self.loc))
                else:
                    il.append(il.set_reg(ARCH_SIZE, RegisterName(reg.name), value, loc=self.loc))
            case RegisterPairOperand(high, low):
                if self._high:
                    il.append(il.set_reg(ARCH_SIZE, RegisterName(high.name), value, loc=self.loc))
                else:
                    self._high = True
                    il.append(il.set_reg(ARCH_SIZE, RegisterName(low.name), value, loc=self.loc))
                    done = False
            case MemoryOperand(_, base, _, _):
                il.append(il.set_reg(ARCH_SIZE, RegisterName(base.name), value, loc=self.loc))
            case ControlRegisterOperand(reg):
                if reg == ControlRegister.PCE1:
                    #HACK: write access is not allowed, used to model jumps
                    branch = il.call
                    if str(self.instruction.operands[0]) == 'B3':
                        branch = il.ret
                    il.append(branch(value, loc=self.loc))
                else:
                    raise NotImplementedError('control register writes')
        if done and is_temp_reg(value, il):
            allocator.free(il.get_expr(value).src) # type: ignore

class LiftInstruction:
    def __init__(self, src: Instruction, il: LowLevelILFunction) -> None:
        self.src = src
        self.inputs: list[InputOperand] = list()
        self._reads = list()
        self._writes = list()
        self.output: OutputOperand | None = None
        self.lift_cycle: int = 0

        if src.opcode not in OPCODE_CALLBACKS:
            self.operation = Operation([], get_unimplemented_cb(il, _addr2loc(src.address)), src)
            return
        
        for operand in src.operands:
            if isinstance(operand, MemoryOperand):
                # Access info for memory operands documents memory access.
                # Here, register access is relevant, which is RW.
                input = InputOperand(operand, il, src)
                # Register access is in first cycle.
                # Memory access is in third cycle, but may be lifted without delay.
                self._reads.append((0, input))
                self.inputs.append(input)
            elif operand.access_info.rw in (RW.none, RW.read, RW.read_write):
                input = InputOperand(operand, il, src)
                if operand.access_info.low_last:
                    self._reads.append((operand.access_info.low_last-1, input))
                if operand.access_info.high_last:
                    self._reads.append((operand.access_info.high_last-1, input))
                self.inputs.append(input)
        self.lift_cycle = max(map(lambda c: c[0], self._reads), default=0)
        self.operation = Operation(self.inputs, OPCODE_CALLBACKS[src.opcode](il, _addr2loc(src.address)), src)
        for operand in src.operands:
            if isinstance(operand, MemoryOperand):
                output = OutputOperand(operand, self.operation, src)
                # Optional address write is in first cycle.
                self._writes.append((0, output))
            elif operand.access_info.rw in (RW.write, RW.read_write):
                self.output = OutputOperand(operand, self.operation, src)
                if operand.access_info.low_first:
                    self._writes.append((operand.access_info.low_first-1, self.output))
                if operand.access_info.high_first:
                    self._writes.append((operand.access_info.high_first-1, self.output))
        if _is_branch(src):
            pc_output = OutputOperand(ControlRegisterOperand(ControlRegister.PCE1), self.operation, src)
            # branches don't work differently, but their delay is similar
            self._writes.append((5, pc_output))

    def is_first(self) -> bool:
        # For load/store in same cycle, load occurs first.
        return self.src.opcode.lower().startswith('ld')

    def get_reads(self) -> list[tuple[int, InputOperand]]:
        return self._reads

    def get_operation(self) -> tuple[int, Operation]:
        return (self.lift_cycle, self.operation)
    
    def get_writes(self) -> list[tuple[int, OutputOperand]]:
        return self._writes

def _addr2loc(address: int) -> ILSourceLocation:
    return ILSourceLocation(address, -1)
 
def _is_branch(instr: Instruction) -> bool:
    return instr.opcode in ('b', 'bpos', 'bdec', 'callp')

def _lift_cycle(ctx: LiftingContext):
    '''Translate one cycle of pipeline execution to IL'''
    for input in ctx.read_queue.dequeue():
        input.read(ctx.read_alloc)
    for operation in ctx.op_queue.dequeue():
        for statement in operation.get_statements():
            ctx.il.append(statement)
        if operation.has_outputs():
            operation.store(ctx.read_alloc, ctx.il)
    for output in ctx.write_queue.dequeue():
        output.write(ctx.il, ctx.temp_alloc)

def _drain_queues(ctx: LiftingContext):
    while any(map(lambda q: len(q) > 0, (ctx.read_queue, ctx.op_queue, ctx.write_queue))):
        _lift_cycle(ctx)

def lift_ep(ctx: LiftingContext, packet: list[Instruction]):
    # 1. Convert instructions to helper objects for lifting
    lift_packet = [LiftInstruction(instr, ctx.il) for instr in packet]

    # 2. Enqueue parts of the EPs instructions in lifting queues
    for lift_instr in lift_packet:
        ctx.read_queue.enqueue(lift_instr.get_reads())
        ctx.op_queue.enqueue([lift_instr.get_operation()], lift_instr.is_first())
        #HACK: front=True is workaround for branch lifting
        ctx.write_queue.enqueue(lift_instr.get_writes(), front=True)
    
    # 3. Translate cycles to IL, multiple in case of multi-cycle NOP
    delay = max(map(get_delay_consumption, packet))
    for _ in range(delay):
        _lift_cycle(ctx)

def lift_instructions(arch, il: LowLevelILFunction , stream: Generator[Instruction, None, None], end: Optional[int]=None):
    ctx = LiftingContext(arch, il, LiftingSettings(False))
    lifted = 0
    while True:
        packet = _next_execution_packet(stream)
        if len(packet) == 0: break
        lift_ep(ctx, packet)
        lifted += sum(map(lambda instr: instr.size, packet))
        if end and packet[-1].address + packet[-1].size > end: break
    _drain_queues(ctx)
    return lifted

def _next_execution_packet(stream: Generator[Instruction, None, None], is_header_based: bool=False) -> List[Instruction]:
    execution_packet = list()
    while True:
        try:
            instr = next(stream)
        except StopIteration:
            break
        #TODO: either break on error or skip instruction based on setting
        if instr.is_invalid(): break
        if instr.is_fp_header(): continue # do not need to lift header
        execution_packet.append(instr)
        if not instr.parallel: break
        if not is_header_based and ((instr.address+4) % (ARCH_SIZE * 8)) == 0:
            break # In versions that are not header-based, EPs cannot span FPs.
    return execution_packet

def lift_basic_block(block: BasicBlock, ctx: LiftingContext) -> int:
    return 0

def lift_function(arch: TMS320C6xBaseArch, function: LowLevelILFunction, context: FunctionLifterContext) -> bool:
    settings = LiftingSettings(header_based=True, simplify=False)

    logger = context._logger
    bv = unwrap(function.view)

    for block in context.blocks:
        function.set_current_source_block(block)
        
        context.prepare_block_translation(function, arch, block.start)
        label = function.get_label_for_address(arch, block.start)
        if label is not None:
            function.mark_label(label)

        begin_instruction_count = len(function)

        # Generate IL for each instruction in the block
        addr = block.start
        while addr < block.end:
            if bv.analysis_is_aborted:
                return False
            
            location = ArchAndAddr(arch, addr)
            function.set_current_address(addr, arch)
            function.clear_indirect_branches()

            opcode = block.get_instruction_data(addr)
            if len(opcode) == 0:
                opcode = bv.read(addr, block.end - addr)
            if len(opcode) == 0:
                function.append(function.undefined())
                logger.log_debug(f'Instruction data not found at {addr:08x}')
                break
            if settings.header_based:
                opcode_end = addr + len(opcode)
                remaining_fp_bytes = (-opcode_end) & FP_SIZE
                opcode += bv.read(opcode_end, remaining_fp_bytes)

            stream = arch.disasm.disasm(opcode, addr)
            lifted_bytes = lift_instructions(arch, function, stream, end=block.end)

            if lifted_bytes is None or lifted_bytes <= 0:
                function.append(function.undefined())
                logger.log_debug(f'Invalid instruction at {addr:08x}')
                break
            addr += lifted_bytes

        function.clear_indirect_branches()
        
    if len(function) == 0:
        # If no instructions, make it undefined
        function.append(function.undefined())
        logger.log_debug(f'No instructions found at {unwrap(function.source_function).start:08x}')

    function.finalize()
    return True


# def lift_basic_block(block: BasicBlock, func: LowLevelILFunction) -> BlockLiftingResult:
# BlockLiftingResult ~= bool, pending instructions

# def lift_function(arch, func: LowLevelILFunction, context: FunctionLifterContext) -> bool:
# Lift entire function after analysis, by lifting basic blocks, their delayed instructions, and their branches.

