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

from binaryninja.architecture import RegisterName
from binaryninja.basicblock import BasicBlock
from binaryninja.commonil import ILSourceLocation
from binaryninja.function import ArchAndAddr
from binaryninja.lowlevelil import ILRegister, LowLevelILReg, LowLevelILFunction, ExpressionIndex, LLIL_REG_IS_TEMP, LLIL_TEMP, LLIL_GET_TEMP_REG_INDEX, LowLevelILLabel
from binaryninja.log import log_warn, log_info, log_debug, log_error
from binaryninja.variable import PossibleValueSet, ValueRange

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional, Sequence, Iterable, Generator, List, TYPE_CHECKING

if TYPE_CHECKING:
    from .arch import TMS320C6xBaseArch
    from .analysis import FunctionContext, BranchContext
from .arch import FunctionLifterContext
from .constants import ARCH_SIZE, HW_SIZE, DW_SIZE, INSTRUCTION_DELAY, FP_SIZE
from .instruction import Disassembler
from .util import get_delay_consumption, is_branch, unwrap, Wrapper
from .disassembler.types import Instruction, Operand, ImmediateOperand, RegisterOperand, MemoryOperand, Register, AddressingMode, RW, FuncUnitsOperand, ControlRegisterOperand, RegisterPairOperand, ControlRegister, ConditionType


## Temporary IL registers

def store_temp(reg_id, value, il: LowLevelILFunction, loc=None):
    tmp = LLIL_TEMP(reg_id)
    il.append(il.set_reg(ARCH_SIZE, tmp, value, loc=loc))

class TempAllocator:
    GLOBAL_BASE = 100

    def __init__(self, arch) -> None:
        self.arch = arch
        self.max_temp_reg:int = 0
        self.free_temp_regs:List[int] = list()
        self.max_global: int = self.GLOBAL_BASE
        self.reserved_regs: dict[str, ILRegister] = dict()
    
    def alloc(self) -> ILRegister:
        if len(self.free_temp_regs) == 0:
            reg_id = self.max_temp_reg
            self.max_temp_reg += 1
        else:
            reg_id = self.free_temp_regs.pop()
        return ILRegister(self.arch, LLIL_TEMP(reg_id))

    def free(self, tmp):
        if LLIL_GET_TEMP_REG_INDEX(tmp) >= self.GLOBAL_BASE: return
        if LLIL_GET_TEMP_REG_INDEX(tmp) in self.free_temp_regs: return
        self.free_temp_regs.append(LLIL_GET_TEMP_REG_INDEX(tmp))

    def get_global(self, key: str) -> ILRegister:
        if key in self.reserved_regs:
            return self.reserved_regs[key]
        else:
            reg = ILRegister(self.arch, LLIL_TEMP(self.max_global))
            self.reserved_regs[key] = reg
            self.max_global += 1
            return reg

def _get_global_key(name: str, address: int, aliases: dict[int, int]) -> str:
    if address in aliases:
        address = aliases[address]
    return f'{name}@{address:08x}'

class RegTempAllocator:
    def __init__(self, il: LowLevelILFunction, temp_alloc: TempAllocator) -> None:
        self.il = il
        self.temp_alloc = temp_alloc
        self.references: dict[ILRegister, int] = dict()
        self.handles: dict[RegisterName, list[RegisterHandle]] = dict()
        self.active_handles: dict[RegisterName, RegisterHandle] = dict()
    
    def alloc(self, name: Optional[RegisterName] = None, loc: ILSourceLocation | None = None) -> RegisterHandle:
        if name is None:
            temp_reg = self.temp_alloc.alloc()
            handle = RegisterHandle(temp_reg.name, temp_reg, self, self.il, loc)
            return handle
        if name in self.active_handles:
            handle = self.active_handles[name]
            reg = handle.reg
            self.references[reg] += 1
        else:
            temp_reg = self.temp_alloc.alloc()
            value = self.il.reg(ARCH_SIZE, name, loc=loc)
            store_temp(temp_reg, value, self.il, loc=loc)
            handle = RegisterHandle(name, temp_reg, self, self.il, loc)
            self.references[temp_reg] = 1
            if not name in self.handles:
                self.handles[name] = list()
            self.handles[name].append(handle)
            self.active_handles[name] = handle
        return handle
    
    def free(self, handle: RegisterHandle):
        reg = handle.reg
        if reg not in self.references:
            self.temp_alloc.free(reg)
        self.references[reg] -= 1
        if self.references[reg] <= 0:
            self.temp_alloc.free(reg)
            del self.references[reg]
            self.handles[handle.name].remove(handle)
            if self.active_handles.get(handle.name, None) == handle:
                del self.active_handles[handle.name]

    def notify_write(self, name: RegisterName):
        if name not in self.active_handles: return
        del self.active_handles[name]

class RegisterHandle:
    def __init__(self, name: RegisterName, reg: ILRegister, allocator: RegTempAllocator, il: LowLevelILFunction, loc: ILSourceLocation | None = None) -> None:
        self.name = name
        self._allocator = allocator
        self.reg = reg
        self._il = il
        self._loc = loc
        self._reg_expr = None
        self._pair_expr = None

    def get(self) -> ExpressionIndex:
        if self._reg_expr is None:
            self._reg_expr = self._il.reg(ARCH_SIZE, self.reg, self._loc)
        return self._reg_expr
    
    def get_pair(self, other: RegisterHandle) -> ExpressionIndex:
        if self._pair_expr is None:
            if self.name < other.name:
                self._pair_expr = self._il.reg_split(ARCH_SIZE, other.reg, self.reg, self._loc)
            else:
                self._pair_expr = other.get_pair(self)
        return self._pair_expr

    def free(self):
        self._allocator.free(self)

def is_temp_reg(expr: ExpressionIndex, il: LowLevelILFunction) -> bool:
    instr = il.get_expr(expr)
    if instr is None: return False
    if isinstance(instr, LowLevelILReg) and isinstance(instr.src, ILRegister):
        return LLIL_REG_IS_TEMP(instr.src)
    return False


## WIP: unified parallel and delayed lifting skeleton

@dataclass(frozen=True)
class LiftingSettings:
    header_based: bool
    simplify: bool = False

class LiftingContext:
    def __init__(self, arch, il: LowLevelILFunction, settings: LiftingSettings) -> None:
        self.arch = arch
        self.il = il
        self.settings = settings
        self.cond_queue:LiftingQueue[LiftInstruction] = LiftingQueue(32)
        self.read_queue:LiftingQueue[InputOperand] = LiftingQueue(32)
        self.op_queue:LiftingQueue[Operation] = LiftingQueue(32)
        self.write_queue:LiftingQueue[OutputOperand] = LiftingQueue(32)
        self.branch_queue:LiftingQueue[LiftBranch] = LiftingQueue(16)
        self.temp_alloc = TempAllocator(arch)
        self.reg_alloc = RegTempAllocator(il, self.temp_alloc)
        self.conditional_handler = ConditionalHandler(il, self.reg_alloc)
        self.aliases: dict[int, int] = dict()
        '''Equivalent instructions at a different address'''

class ILBranchType(Enum):
    Jump = 0
    Call = 1
    Tailcall = 2
    Return = 3
    UNDETERMINED = -1

def _get_bin_op_cb(il: LowLevelILFunction, op, loc: ILSourceLocation):
    def __lift(src1: ExpressionIndex, src2: ExpressionIndex) -> Sequence[ExpressionIndex]:
        result = op(ARCH_SIZE, src1, src2, loc=loc)
        return (result,)
    return __lift

def get_add_cb(il: LowLevelILFunction, loc: ILSourceLocation):
    return _get_bin_op_cb(il, il.add, loc)

def get_load_cb(il: LowLevelILFunction, loc: ILSourceLocation, size=ARCH_SIZE, signed=True):
    def __lift(address: ExpressionIndex) -> Sequence[ExpressionIndex]:
        expr = il.load(size, address, loc=loc)
        if size < ARCH_SIZE:
            if signed:
                expr = il.sign_extend(ARCH_SIZE, expr, loc=loc)
            else:
                expr = il.zero_extend(ARCH_SIZE, expr, loc=loc)
        return (expr,)
    return __lift

def get_store_cb(il: LowLevelILFunction, loc: ILSourceLocation, size=ARCH_SIZE):
    def __lift(value: ExpressionIndex, address: ExpressionIndex) -> Sequence[ExpressionIndex]:
        stmt = il.store(size, address, value, loc=loc)
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
    'addkpc': lambda *_: (lambda inp, *_: (inp,)),
    'and': lambda il, loc: _get_bin_op_cb(il, il.and_expr, loc),
    'b': lambda *_: (lambda inp: (inp,)),
    'bnop': lambda *_: (lambda inp, *_: (inp,)),
    'cmpeq': lambda il, loc: _get_bin_op_cb(il, il.compare_equal, loc),
    'cmpgt': lambda il, loc: _get_bin_op_cb(il, il.compare_signed_greater_than, loc),
    'cmpgtu': lambda il, loc: _get_bin_op_cb(il, il.compare_unsigned_greater_than, loc),
    'cmplt': lambda il, loc: _get_bin_op_cb(il, il.compare_signed_less_than, loc),
    'cmpltu': lambda il, loc: _get_bin_op_cb(il, il.compare_unsigned_less_than, loc),
    'ldb': lambda il, loc: get_load_cb(il, loc, 1),
    'ldbu': lambda il, loc: get_load_cb(il, loc, 1, False),
    'ldh': lambda il, loc: get_load_cb(il, loc, HW_SIZE),
    'ldhu': lambda il, loc: get_load_cb(il, loc, HW_SIZE, False),
    'ldndw': lambda il, loc: get_load_cb(il, loc, DW_SIZE),
    'lddw': lambda il, loc: get_load_cb(il, loc, DW_SIZE),
    'ldnw': get_load_cb,
    'ldw': get_load_cb,
    'mv': lambda *_: (lambda inp: (inp,)),
    'mvk': lambda *_: (lambda inp: (inp,)),
    'mvkh': lambda *_: (lambda inp: (inp,)),
    'nop': lambda il, loc: (lambda *_: (il.nop(loc=loc),)),
    'or': lambda il, loc: _get_bin_op_cb(il, il.or_expr, loc),
    'shl': lambda il, loc: _get_bin_op_cb(il, il.shift_left, loc),
    'shr': lambda il, loc: _get_bin_op_cb(il, il.arith_shift_right, loc),
    'shru': lambda il, loc: _get_bin_op_cb(il, il.logical_shift_right, loc),
    'stb': lambda il, loc: get_store_cb(il, loc, 1),
    'stdw': lambda il, loc: get_store_cb(il, loc, DW_SIZE),
    'sth': lambda il, loc: get_store_cb(il, loc, HW_SIZE),
    'stndw': lambda il, loc: get_store_cb(il, loc, DW_SIZE),
    'stnw': get_store_cb,
    'stw': get_store_cb,
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

class ConditionalHandler:
    def __init__(self, il: LowLevelILFunction, allocator: RegTempAllocator) -> None:
        self._il = il
        self._allocator = allocator
        self._assignments: dict[int, RegisterHandle] = dict()
        self._active_condition: Optional[ConditionType] = None

    @staticmethod
    def _is_conditional(instr: LiftInstruction) -> bool:
        return instr.condition.register is not None
    
    def _free(self, instr: LiftInstruction, part: InputOperand | Operation | Output):
        if isinstance(part, InputOperand): return
        if not instr.is_last_part(part): return
        self._assignments[instr.address].free()
        del self._assignments[instr.address]
    
    def _get_condition_expr(self, instr: LiftInstruction) -> ExpressionIndex:
        zero = self._il.const(ARCH_SIZE, 0)
        cond_reg = self._assignments[instr.address].get()
        if instr.condition.branch:
            cond = self._il.compare_not_equal(ARCH_SIZE, cond_reg, zero, instr.loc)
        else:
            cond = self._il.compare_equal(ARCH_SIZE, cond_reg, zero, instr.loc)
        return cond
    
    def end_conditional(self):
        if self._active_condition is not None:
            self._il.mark_label(self._false_label)
        self._active_condition = None

    def process(self, instr: LiftInstruction):
        if not self._is_conditional(instr): return
        assert instr.address not in self._assignments
        condition_name = RegisterName(unwrap(instr.condition.register).name)
        self._assignments[instr.address] = self._allocator.alloc(condition_name, instr.loc)

    def before_lifting(self, part: InputOperand | Operation | Output, parallel: bool = False):
        instr = part.parent
        if not self._is_conditional(instr):
            self.end_conditional()
            return
        assert instr.address in self._assignments
        il = self._il
        if parallel and self._active_condition == ConditionType(instr.condition ^ 1):
            end_label = LowLevelILLabel()
            il.append(il.goto(end_label))
            il.mark_label(self._false_label)
            self._active_condition = instr.condition
            self._false_label = end_label
        elif self._active_condition != instr.condition:
            self.end_conditional()
            true_case = LowLevelILLabel()
            false_case = LowLevelILLabel()
            operand = self._get_condition_expr(instr)
            il.append(il.if_expr(operand, true_case, false_case, instr.loc))
            il.mark_label(true_case)
            self._active_condition = instr.condition
            self._false_label = false_case
        # self._free(instr, part)

class InputOperand:
    def __init__(self, src: Operand, il: LowLevelILFunction, parent: LiftInstruction) -> None:
        self.src = src
        self.parent = parent
        self._il = il
        self._handles:dict[RegisterName, RegisterHandle] = dict()
        self._is_read = False
        self._low = True

    def read(self, allocator: RegTempAllocator):
        if self._is_read: return
        # allocate register reads and store handles
        self._is_read = True
        match self.src:
            case RegisterOperand(reg) | ControlRegisterOperand(reg):
                reg_name = RegisterName(reg.name)
                self._handles[reg_name] = allocator.alloc(reg_name, self.parent.loc)
            case RegisterPairOperand(high, low):
                if self._low:
                    self._is_read = self._low = False
                    reg_name = RegisterName(low.name)
                else:
                    reg_name = RegisterName(high.name)
                self._handles[reg_name] = allocator.alloc(reg_name, self.parent.loc)
            case MemoryOperand(_, base, offset, _):
                reg_name = RegisterName(base.name)
                self._handles[reg_name] = allocator.alloc(reg_name, self.parent.loc)
                if isinstance(offset, Register):
                    reg_name = RegisterName(offset.name)
                    self._handles[reg_name] = allocator.alloc(reg_name, self.parent.loc)

    def to_expr(self) -> ExpressionIndex:
        il = self._il
        match self.src:
            case ImmediateOperand(value):
                return il.const(ARCH_SIZE, value, loc=self.parent.loc)
            case RegisterOperand(reg) | ControlRegisterOperand(reg):
                reg_handle = self._handles[RegisterName(reg.name)]
                return reg_handle.get()
            case RegisterPairOperand(high, low):
                hi = self._handles[RegisterName(high.name)]
                lo = self._handles[RegisterName(low.name)]
                return lo.get_pair(hi)
            case MemoryOperand(mode, base, offset, scaled):
                base = self._handles[RegisterName(base.name)]
                base_il = base.get()
                if isinstance(offset, Register):
                    offset = self._handles[RegisterName(offset.name)]
                    offset_il = offset.get()
                else:
                    offset_il = self._il.const(ARCH_SIZE, offset, loc=self.parent.loc)
                if scaled:
                    offset_il = il.mult(ARCH_SIZE, 
                        offset_il,
                        il.const(ARCH_SIZE, self.src.access_info.size, loc=self.parent.loc),
                        loc=self.parent.loc)
                match mode:
                    case (AddressingMode.NEG_OFFSET
                            | AddressingMode.PREDECREMENT
                            | AddressingMode.POSTDECREMENT):
                        offset_il = il.neg_expr(ARCH_SIZE, offset_il, loc=self.parent.loc)
                self.address_expr = il.add(ARCH_SIZE, base_il, offset_il, loc=self.parent.loc)
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
    
    def is_reg_derived(self) -> bool:
        return self._is_read and len(self._handles) > 0

    def free(self):
        for handle in self._handles.values():
            handle.free()
        self._handles.clear()

class Operation:
    def __init__(self, inputs: list[InputOperand], callback, parent: LiftInstruction) -> None:
        self.inputs = inputs
        self.callback = callback
        self.parent = parent
        self._outputs: list[Output] = list()
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

    def register_output(self, output: 'Output'):
        self._outputs.append(output)

    def store(self, allocator: RegTempAllocator, il: LowLevelILFunction):
        if self._stored: return
        self._lift()
        if any([i.is_reg_derived() for i in self.inputs]):
            stored_exprs = list()
            for output_expr in self._output_exprs:
                temp_handle = allocator.alloc(loc=self.parent.loc)
                store_temp(temp_handle.reg, output_expr, il, loc=self.parent.loc)
                stored_exprs.append(temp_handle.get())
            self._output_exprs = stored_exprs
        self._stored = True
        for input in self.inputs:
            input.free()

    def get_statements(self) -> Iterable[ExpressionIndex]:
        self._lift()
        return self._statements

    def get(self, output: 'Output') -> ExpressionIndex:
        if output not in self._outputs:
            raise ValueError('requested output needs to be registered')
        self._lift()
        return self._output_exprs[self._outputs.index(output)]
    
    def has_outputs(self) -> bool:
        return len(self._outputs) > 0

class OutputOperand:
    def __init__(self, src:Operand, operation: Operation, parent: LiftInstruction) -> None:
        self.src = src
        self.operation = operation
        self.parent = parent
        self._high = False
        if self._is_writing():
            operation.register_output(self)
            self._done_ = False
        else:
            self._done_ = True

    def _is_writing(self) -> bool:
        match self.src:
            case MemoryOperand(mode, _):
                if mode in (AddressingMode.NEG_OFFSET, AddressingMode.POS_OFFSET):
                    return False
        return True

    def write(self, il:LowLevelILFunction, temp_alloc: TempAllocator, reg_alloc: RegTempAllocator):
        if not self._is_writing(): return
        value = self.operation.get(self)
        done = True
        match self.src:
            case RegisterOperand(reg):
                reg_alloc.notify_write(RegisterName(reg.name))
                if self.parent.src.opcode in ('mvkh', 'mvklh'):
                    il.append(il.set_reg(HW_SIZE, RegisterName(reg.name+'H'), value, loc=self.parent.loc))
                else:
                    il.append(il.set_reg(ARCH_SIZE, RegisterName(reg.name), value, loc=self.parent.loc))
            case RegisterPairOperand(high, low):
                if self._high:
                    reg_alloc.notify_write(RegisterName(high.name))
                    il.append(il.set_reg(ARCH_SIZE, RegisterName(high.name), value, loc=self.parent.loc))
                else:
                    self._high = True
                    reg_alloc.notify_write(RegisterName(low.name))
                    il.append(il.set_reg(ARCH_SIZE, RegisterName(low.name), value, loc=self.parent.loc))
                    done = False
            case MemoryOperand(_, base, _, _):
                reg_alloc.notify_write(RegisterName(base.name))
                il.append(il.set_reg(ARCH_SIZE, RegisterName(base.name), value, loc=self.parent.loc))
            case ControlRegisterOperand(reg):
                    raise NotImplementedError('control register writes')
        self._done_ = done
        if done and is_temp_reg(value, il):
            temp_alloc.free(il.get_expr(value).src) # type: ignore

    @property
    def done(self) -> bool:
        return self._done_

class LiftInstruction:
    def __init__(self, src: Instruction, ctx: LiftingContext) -> None:
        self.src = src
        il = ctx.il
        self._ctx = ctx
        self._il = il
        self._inputs: list[InputOperand] = list()
        self._reads = list()
        self._writes = list()
        self._lift_cycle: int = 0
        self._last_output: int = -1
        self._loc_ = _addr2loc(src.address)
        self.__unimplemented = True

        if src.opcode not in OPCODE_CALLBACKS:
            self._operation = Operation([], get_unimplemented_cb(il, _addr2loc(src.address)), self)
            return
        self.__unimplemented = False
        

        for operand in src.operands:
            if isinstance(operand, MemoryOperand):
                # Access info for memory operands documents memory access.
                # Here, register access is relevant, which is RW.
                input = InputOperand(operand, il, self)
                # Register access is in first cycle.
                # Memory access is in third cycle, but may be lifted without delay.
                self._reads.append((0, input))
                self._inputs.append(input)
            elif operand.access_info.rw in (RW.none, RW.read, RW.read_write):
                input = InputOperand(operand, il, self)
                if operand.access_info.low_last:
                    self._reads.append((operand.access_info.low_last-1, input))
                if operand.access_info.high_last:
                    self._reads.append((operand.access_info.high_last-1, input))
                self._inputs.append(input)
        self._lift_cycle = max(map(lambda c: c[0], self._reads), default=0)
        self._operation = Operation(self._inputs, OPCODE_CALLBACKS[src.opcode](il, _addr2loc(src.address)), self)
        for operand in src.operands:
            if isinstance(operand, MemoryOperand):
                output = OutputOperand(operand, self._operation, self)
                # Optional address write is in first cycle.
                self._writes.append((0, output))
                self._last_output = max(self._last_output, 0)
            elif operand.access_info.rw in (RW.write, RW.read_write):
                output = OutputOperand(operand, self._operation, self)
                if operand.access_info.low_first:
                    write_cycle = operand.access_info.low_first-1
                    self._writes.append((operand.access_info.low_first-1, output))
                    self._last_output = max(self._last_output, write_cycle)
                if operand.access_info.high_first:
                    write_cycle = operand.access_info.high_first-1
                    self._writes.append((write_cycle, output))
                    self._last_output = max(self._last_output, write_cycle)
        if is_branch(src):
            self._last_output = max(self._last_output, 5)

    def _has_output(self) -> bool:
        return self._lift_cycle <= self._last_output

    def is_first(self) -> bool:
        # For load/store in same cycle, load occurs first.
        return self.src.opcode.lower().startswith('ld')

    def get_reads(self) -> list[tuple[int, InputOperand]]:
        return self._reads

    def get_operation(self) -> tuple[int, Operation]:
        return (self._lift_cycle, self._operation)
    
    def get_writes(self) -> list[tuple[int, OutputOperand]]:
        return self._writes
    
    def get_branch(self) -> Optional[LiftBranch]:
        if is_branch(self.src) and not self.__unimplemented:
            branch_type = ILBranchType.Jump
            if str(self.src.operands[0]) == 'B3':
                branch_type = ILBranchType.Return
            def cb() -> ExpressionIndex:
                return self._operation.get(branch)
            branch = LiftBranch(self, Wrapper(cb), branch_type, self._ctx)
            self._operation.register_output(branch)
            return branch
        return None
    
    def is_last_part(self, part: Operation | Output) -> bool:
        if part == self._operation:
            return not self._has_output()
        elif part in map(lambda w: w[1], self._writes):
            assert isinstance(part, OutputOperand)
            if not part.done: return False
            is_max_write = part == max(reversed(self._writes), key=lambda w: w[0])[1]
            return is_max_write and not is_branch(self.src)
        elif isinstance(part, LiftBranch) and part.parent == self:
            return True # branches are always last part of an instruction
        return False
    
    @property
    def address(self) -> int:
        return self.src.address
    
    @property
    def condition(self) -> ConditionType:
        return self.src.condition
    
    @property
    def loc(self) -> ILSourceLocation:
        return self._loc_

class LiftPartial(LiftInstruction):
    def __init__(self, src: Instruction, ctx: LiftingContext, condition: Optional[ConditionType] = None) -> None:
        self.src = src
        il = ctx.il
        self._ctx = ctx
        self._il = il
        self._inputs: list[InputOperand] = list()
        self._reads = list()
        self._writes = list()
        self._lift_cycle: int = 0
        self._last_output: int = -1
        self._loc_ = _addr2loc(src.address)
        self._operation = None
        if condition is None:
            self._condition_ = src.condition
        else:
            self._condition_ = unwrap(condition)
    
    @property
    def condition(self) -> ConditionType:
        return self._condition_

class LiftBranch:
    def __init__(self, parent: LiftInstruction, target: Wrapper[ExpressionIndex], branch_type: ILBranchType, ctx: LiftingContext, delay: int = 5) -> None:
        self.parent = parent
        self.target = target
        self.type = branch_type
        self.ctx = ctx
        self.delay = delay
    
    def lift(self):
        il = self.ctx.il
        # Similar may be required for indirect branches with known targets
        target_op = self.parent.src.operands[0]
        if isinstance(target_op, ImmediateOperand):
            il.set_indirect_branches([(self.ctx.arch, target_op.value)])
        target = self.target.get()
        match self.type:
            case ILBranchType.Jump:
                branch = il.jump
            case ILBranchType.Call:
                branch = il.call
            case ILBranchType.Return:
                branch = il.ret
            case ILBranchType.Tailcall:
                branch = il.tailcall
        il.append(branch(target, loc=self.parent.loc))

type Output = OutputOperand | LiftBranch

def _addr2loc(address: int) -> ILSourceLocation:
    return ILSourceLocation(address, -1)

def _store_branch(branch: LiftBranch, ctx: LiftingContext):
    instr = branch.parent
    target = ctx.temp_alloc.get_global(
            _get_global_key('target', instr.address, ctx.aliases))
    store_temp(target, branch.target.get(), ctx.il, instr.loc)

def _lift_cycle(ctx: LiftingContext, store_branches: bool = False):
    '''Translate one cycle of pipeline execution to IL'''
    for instr in ctx.cond_queue.dequeue():
        ctx.conditional_handler.process(instr)
    for input in ctx.read_queue.dequeue():
        ctx.conditional_handler.before_lifting(input)
        input.read(ctx.reg_alloc)
    for operation in ctx.op_queue.dequeue():
        ctx.conditional_handler.before_lifting(operation)
        for statement in operation.get_statements():
            ctx.il.append(statement)
        if operation.has_outputs():
            operation.store(ctx.reg_alloc, ctx.il)
    for output in ctx.write_queue.dequeue():
        ctx.conditional_handler.before_lifting(output)
        output.write(ctx.il, ctx.temp_alloc, ctx.reg_alloc)
    first_branch = True
    for branch in ctx.branch_queue.dequeue():
        if store_branches:
            ctx.conditional_handler.before_lifting(branch)
            _store_branch(branch, ctx)
        else:
            if branch.type != ILBranchType.Call:
                # HACK: ugly, but remaining IL needs to be added before non-call
                _drain_queues(ctx, True)
            ctx.conditional_handler.before_lifting(branch, not first_branch)
            branch.lift()
            first_branch = False # same cycle branches should be parallel

def _drain_queues(ctx: LiftingContext, store_branches: bool = False):
    while any(map(lambda q: len(q) > 0, (ctx.read_queue, ctx.op_queue, ctx.write_queue, ctx.branch_queue))):
        _lift_cycle(ctx, store_branches)

def lift_ep(ctx: LiftingContext, packet: list[Instruction]):
    # 1. Convert instructions to helper objects for lifting
    lift_packet = [LiftInstruction(instr, ctx) for instr in packet
            if not instr.is_fp_header()] # do not lift headers

    # 2. Enqueue parts of the EPs instructions in lifting queues
    for lift_instr in lift_packet:
        ctx.cond_queue.enqueue([(0, lift_instr)])
        ctx.read_queue.enqueue(lift_instr.get_reads())
        ctx.op_queue.enqueue([lift_instr.get_operation()], lift_instr.is_first())
        ctx.write_queue.enqueue(lift_instr.get_writes())
        branch = lift_instr.get_branch()
        if branch:
            ctx.branch_queue.enqueue([(branch.delay, branch)])
    
    # 3. Translate cycles to IL, multiple in case of multi-cycle NOP
    delay = max(map(get_delay_consumption, packet))
    for _ in range(delay):
        _lift_cycle(ctx)

def lift_instructions(arch, il: LowLevelILFunction , stream: Generator[Instruction, None, None], end: Optional[int]=None, ctx: Optional[LiftingContext]=None) -> int:
    if ctx is None:
        ctx = LiftingContext(arch, il, LiftingSettings(False))
    lifted = 0
    while True:
        packet = _next_execution_packet(stream, ctx.settings.header_based)
        if len(packet) == 0: break
        lift_ep(ctx, packet)
        lifted += sum(map(lambda instr: instr.size, packet))
        if end and packet[-1].address + packet[-1].size >= end: break
    _drain_queues(ctx)
    ctx.conditional_handler.end_conditional()
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
        execution_packet.append(instr)
        if instr.is_fp_header(): continue # add header but ignore it later
        if not instr.parallel: break
        if not is_header_based and ((instr.address+4) % (ARCH_SIZE * 8)) == 0:
            break # In versions that are not header-based, EPs cannot span FPs.
    return execution_packet

def _queue_pending_branches(pending_branches: list[BranchContext], ctx: LiftingContext):
    for branch_context in pending_branches:
        if branch_context.type == ILBranchType.UNDETERMINED: continue
        instr = LiftPartial(branch_context.src, ctx, branch_context.condition)
        def cb() -> ExpressionIndex:
            target = ctx.temp_alloc.get_global(
                    _get_global_key('target', instr.address, ctx.aliases))
            return ctx.il.reg(ARCH_SIZE, target, instr.loc)
        branch = LiftBranch(instr, Wrapper(cb), branch_context.type, ctx, branch_context.delay)
        ctx.branch_queue.enqueue([(branch.delay, branch)])

def lift_basic_block(ctx: LiftingContext, stream: Generator[Instruction, None, None], pending_branches: list[BranchContext], end: Optional[int]=None) -> int:
    _queue_pending_branches(pending_branches, ctx)
    lifted = 0
    while True:
        packet = _next_execution_packet(stream, ctx.settings.header_based)
        if len(packet) == 0: break
        lift_ep(ctx, packet)
        lifted += sum(map(lambda instr: instr.size, packet))
        if end and packet[-1].address + packet[-1].size >= end: break
    _drain_queues(ctx, True)
    ctx.conditional_handler.end_conditional()
    return lifted

def lift_function(arch: TMS320C6xBaseArch, function: LowLevelILFunction, context: FunctionLifterContext) -> bool:
    settings = LiftingSettings(header_based=True, simplify=False)
    ctx = LiftingContext(arch, function, settings)
    function_context: FunctionContext = context.function_arch_context
    ctx.aliases = function_context.aliases

    logger = context._logger
    bv = unwrap(function.view)

    for block in context.blocks:
        function.add_label_for_address(arch, block.start)

    for block in context.blocks:
        function.set_current_source_block(block)
        
        context.prepare_block_translation(function, arch, block.start)
        label = function.get_label_for_address(arch, block.start)
        function.mark_label(unwrap(label))

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
                function.append(function.undefined(loc=_addr2loc(addr)))
                logger.log_debug(f'Instruction data not found at {addr:08x}')
                break
            if settings.header_based:
                opcode_end = addr + len(opcode)
                remaining_fp_bytes = (-opcode_end) % FP_SIZE
                if remaining_fp_bytes: log_debug(f'Reading FP remainder at {opcode_end:08x}')
                opcode += bv.read(opcode_end, remaining_fp_bytes)

            if addr in function_context.branches:
                pending_branches = function_context.branches[addr]
            else:
                pending_branches = []

            stream = arch.disasm.disasm(opcode, addr)
            lifted_bytes = lift_basic_block(ctx, stream, pending_branches, end=block.end)

            if lifted_bytes is None or lifted_bytes <= 0:
                function.append(function.undefined(loc=_addr2loc(addr)))
                logger.log_debug(f'Invalid instruction at {addr:08x}')
                break
            addr += lifted_bytes

        end_instruction_count = len(function)
        function.clear_indirect_branches()
        segment = unwrap(bv.get_segment_at(block.end - 1))

        if begin_instruction_count == end_instruction_count:
            function.append(function.undefined(loc=_addr2loc(addr)))
            logger.log_debug(f'Basic block must have instructions to be valid, at {block.start:08x}')
        elif ((len(block.outgoing_edges) == 0 and not block.can_exit and not block.fallthrough_to_function) or block.end == segment.end):
            #HACK: workaround to stop lifting 
            function.append(function.no_ret(loc=_addr2loc(block.end)))
        else:
            exit_label = function.get_label_for_address(arch, block.end)
            if exit_label:
                function.append(function.goto(exit_label, loc=_addr2loc(block.end)))
            else:
                function.append(function.jump(function.const(ARCH_SIZE, block.end), loc=_addr2loc(block.end)))

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

