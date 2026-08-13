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

from binaryninja.architecture import BasicBlockAnalysisContext, InstructionBranch
from binaryninja.basicblock import BasicBlock
from binaryninja.binaryview import BinaryView
from binaryninja.enums import BranchType, FunctionAnalysisSkipOverride
from binaryninja.function import ArchAndAddr, Function
from binaryninja.log import log_info, log_debug


from dataclasses import dataclass
from typing import NamedTuple, Optional, Sequence

from tms320c6x_disassembler.types import ConditionType, Instruction, RegisterOperand, Register, RW
from .condition import ConditionState
from ..constants import ARCH_SIZE, FP_SIZE, HW_SIZE, BRANCH_DELAY
from ..lifting import ILBranchType
from ..log import Logger
from ..util import get_delay_consumption, unwrap


@dataclass
class SploopState:
    active: bool = False
    loop_instr: Optional[Instruction] = None
    start: int = 0
    end: int = 0

    def process(self, i: Instruction):
        if i.opcode.startswith('sploop'):
            assert not self.active
            self.active = True
            self.loop_instr = i
        elif i.opcode.startswith('spkernel'):
            self.end = i.address
        if self.active and self.start == 0 and not i.parallel:
            self.start = i.address + i.size

class BlockState:
    def __init__(self, block: BasicBlock) -> None:
        self.block: BasicBlock = block
        self.packet: int = 0
        self.ep_lengths: list[int] = list()
        self.sploop: SploopState = SploopState()
        self.conditions: ConditionState = ConditionState()

    def process(self, ep: list[Instruction], raw: bytes):
        for i in ep:
            self.sploop.process(i)
            self.conditions.process(i)
        self.packet += 1
        self.ep_lengths.append(len(raw))
        
@dataclass
class BranchContext:
    condition: ConditionType
    delay: int
    type: ILBranchType
    src: Instruction

class FunctionContext:
    def __init__(self) -> None:
        self.headers: dict[int, bytes] = dict()
        self.sploop_ii: dict[int, int] = dict()
        self.branches: dict[int, list[BranchContext]] = dict()
        self.aliases: dict[int, int] = dict()

class QueuedBranch(NamedTuple):
    condition: ConditionType
    branch: InstructionBranch
    instruction: Instruction
    delay: int
BranchSlot = Sequence[QueuedBranch]
class AnalyzedBranch(NamedTuple):
    condition: ConditionType
    edge_type: BranchType
    branch: InstructionBranch
    instruction: Instruction | None
    delay: int
UnifiedSlot = Sequence[AnalyzedBranch]
PendingBranches = list[BranchSlot]

def analyze_basic_blocks(arch, func: Function, 
        context: BasicBlockAnalysisContext) -> None:
    #TODO: sound error handling
    view = func.view
    blocks_to_process: list[ArchAndAddr] = list()
    instr_blocks: dict[ArchAndAddr, BasicBlock] = dict()
    seen_blocks: set[ArchAndAddr] = set()
    block_carried_branches: dict[ArchAndAddr, PendingBranches] = dict()
    sploop_blocks: dict[ArchAndAddr, SploopState] = dict()

    # Start by processing the entry point of the function
    start = func.start
    blocks_to_process.append(ArchAndAddr(arch, start))
    seen_blocks.add(ArchAndAddr(arch, start))

    function_context: FunctionContext = FunctionContext()
    specified_branches: dict[int, list[BranchContext]] = dict()
    context.function_arch_context = function_context

    total_size = 0
    if context.analysis_skip_override == FunctionAnalysisSkipOverride.AlwaysSkipFunctionAnalysis:
        max_size = 0
    else:
        max_size = context.max_function_size
    max_size_reached = False

    while len(blocks_to_process) > 0:
        if view.analysis_is_aborted: return

        # Get the next block to process
        location = blocks_to_process.pop(0)
        # if not __addr_is_executable(view, location.addr):
        #     continue

        # Create a new basic block
        block: BasicBlock = context.create_basic_block(location.arch, location.addr) # type: ignore
        assert block is not None
        s = BlockState(block)

        # This architecture interpretes a delay slot as a cycle.
        # Due to parallelism and idling instructions,
        # the number of instructions per delay cycle may vary.
        # For basic block analysis, delay is mostly relevant for branch instructions.
        pending_branches: PendingBranches = list()
        if location in block_carried_branches:
            pending_branches = block_carried_branches[location]
            __add_branches_to_context(function_context, location.addr, pending_branches)
        last_return_write = 255
        if location in sploop_blocks:
            s.sploop = sploop_blocks[location]

        # Disassemble the instructions in the block
        ends_block = False
        while True:
            if view.analysis_is_aborted: break
            #TODO: split blocks when processing jump into block middle
            if location in instr_blocks:
                target_block = instr_blocks[location]
                if target_block.start == location.addr:
                    block.add_pending_outgoing_edge(BranchType.UnconditionalBranch, location.addr, arch, block.start != location.addr)
                    break
                else:
                    split_block = context.create_basic_block(location.arch, location.addr)
                    assert split_block is not None
                    instr_data = target_block.get_instruction_data(location.addr)
                    split_block.add_instruction_data(instr_data)
                    split_block.fallthrough_to_function = target_block.fallthrough_to_function
                    split_block.has_undetermined_outgoing_edges = target_block.has_undetermined_outgoing_edges
                    split_block.can_exit = target_block.can_exit
                    split_block.end = target_block.end

                    target_block.fallthrough_to_function = False
                    target_block.has_undetermined_outgoing_edges = False
                    target_block.can_exit = True
                    target_block.end = location.addr
                    __update_context(function_context, location.addr, view)

                    for addr in range(location.addr, split_block.end, HW_SIZE):
                        k = ArchAndAddr(arch, addr)
                        if k in instr_blocks:
                            instr_blocks[k] = split_block

                    for e in target_block.get_pending_outgoing_edges():
                        split_block.add_pending_outgoing_edge(e.type, e.target, e.arch, e.fallthrough)
                    target_block.clear_pending_outgoing_edges()
                    target_block.add_pending_outgoing_edge(BranchType.UnconditionalBranch, location.addr, arch, True)

                    #TODO: check for pending branches at split point
                    __add_branches_to_context(function_context, location.addr, pending_branches)
                    __transfer_specified_branches(function_context, specified_branches.get(target_block.start, []), location.addr)

                    seen_blocks.add(location)
                    context.add_basic_block(split_block)
                    block.add_pending_outgoing_edge(BranchType.UnconditionalBranch, location.addr, arch)
                    break

            #TODO: change reads to max_instr_length when workaround is removed

            # Build execution packet by reading parallel instructions until end of fetch packet.
            ep: list[Instruction] = list()
            ep_bytes = b""
            ep_location = location
            new_branches = list()
            is_parallel = False
            while True:
                instr_bytes = view.read(location.addr, arch.max_instr_length)
                if len(instr_bytes) == 0:
                    ends_block = True
                    break

                info = arch.get_instruction_info(instr_bytes, location.addr)

                instr_blocks[location] = block
                ep_bytes += instr_bytes[:info.length]
                instr = arch.disasm.decode(instr_bytes, location.addr)
                if instr.is_invalid(): break # error case
                if not instr.is_fp_header():
                    is_parallel = instr.parallel
                    ep.append(instr)

                for branch in info.branches:
                    new_branch = (info.branch_delay, instr, branch)
                    new_branches.append(new_branch)

                next_func_addr = view.get_next_function_start_after(location.addr)
                next_section_end = view.get_sections_at(location.addr)[0].end
                location = ArchAndAddr(arch, location.addr + info.length)
                # ends_block |= next_func_addr <= location.addr
                ends_block |= next_section_end <= location.addr
                #TODO: fall through to next function?
                header_next = (instr.header is not None and 
                    (location.addr + ARCH_SIZE) % FP_SIZE == 0)
                if (not(is_parallel or header_next) or ends_block): break
            block.add_instruction_data(ep_bytes)
            s.process(ep, ep_bytes)
            if len(new_branches):
                for delay, instr, branch in new_branches:
                    while len(pending_branches) <= delay:
                        pending_branches.append(list())
                    pending_branches[delay].append(QueuedBranch(instr.condition, branch, instr, delay))

            #TODO: handle function branches and branches with pending delay
            def handle_branch(analyzed_branch: AnalyzedBranch, returns: bool, carried_branches: PendingBranches):
                branch = analyzed_branch.branch
                src = analyzed_branch.instruction
                log_debug(f'Handling {branch.type.name} @{location.addr:08x} to {branch.target:08x} (return? {returns})')
                nonlocal ends_block
                target_type = analyzed_branch.edge_type

                # TODO: types and unification are off for conditional calls (and skipped targets)
                match branch.type:
                    case BranchType.UnconditionalBranch|BranchType.TrueBranch:
                        ends_block = True
                        if branch.target == 0: return
                        assert branch.target
                        target = ArchAndAddr(arch, branch.target)

                        if view.should_skip_target_analysis(location, func, location.addr, target):
                            return

                        if is_likely_call(branch, carried_branches, returns):
                            assert len(carried_branches) == 0
                            target_type = BranchType.CallDestination
                            block.add_pending_outgoing_edge(target_type, branch.target, arch)
                            ends_block = not returns
                        else:
                            block.add_pending_outgoing_edge(target_type, branch.target, arch)
                            add_target_to_process(branch.target, carried_branches)
                        __specify_branch_type(function_context, block.start, target_type, unwrap(src), ends_block, specified_branches)
                    case BranchType.IndirectBranch:
                        ends_block = True
                        if is_likely_call(branch, carried_branches, returns):
                            assert len(carried_branches) == 0
                            target_type = BranchType.CallDestination
                            ends_block = not returns
                        for indirect_branch in context.indirect_branches:
                            if (indirect_branch.source_addr != location.addr):
                                continue
                            block.add_pending_outgoing_edge(target_type, indirect_branch.dest_addr, arch)
                            add_target_to_process(indirect_branch.dest_addr, carried_branches)
                        __specify_branch_type(function_context, block.start, target_type, unwrap(src), ends_block, specified_branches)
                    case BranchType.FalseBranch:
                        assert branch.target == 0
                        # fallthrough false condition
                        ends_block = True
                        target = location.addr
                        block.add_pending_outgoing_edge(target_type, target, arch, True)
                        if s.sploop.active:
                            sploop_blocks[location] = s.sploop
                        add_target_to_process(target, carried_branches)
                    case BranchType.FunctionReturn:
                        ends_block = True
                        block.can_exit = True
                    case BranchType.UserDefinedBranch:
                        ends_block = True
                        if s.sploop.loop_instr is None:
                            s.sploop.start = block.start
                        else:
                            function_context.sploop_ii[s.sploop.end] = s.sploop.loop_instr.operands[0].value # type: ignore
                        # used for SPLOOP exit branches
                        block.add_pending_outgoing_edge(
                            BranchType.TrueBranch,
                            s.sploop.start,
                            arch)
                        block.add_pending_outgoing_edge(
                            BranchType.FalseBranch,
                            location.addr,
                            arch)
                        add_target_to_process(s.sploop.start, carried_branches)
                        add_target_to_process(location.addr, carried_branches)
                        s.sploop.active = False
                        s.sploop.start = 0
            
            def is_likely_call(branch: InstructionBranch, carried_branches: PendingBranches,
                    returns: bool) -> bool:
                # This address is not helpful if symbols for basic blocks exist
                # next_func_addr = view.get_next_function_start_after(location.addr)
                is_in_function = func.lowest_address <= branch.target
                return (len(carried_branches) == 0 and 
                    (not is_in_function or returns))

            def add_target_to_process(addr: int, carried_branches: PendingBranches):
                target = ArchAndAddr(arch, addr)
                if target not in seen_blocks:
                    blocks_to_process.append(target)
                    seen_blocks.add(target)
                if target not in block_carried_branches:
                    block_carried_branches[target] = carried_branches
                else:
                    for carried_a, carried_b in zip(block_carried_branches[target],carried_branches):
                        for branch_a, branch_b in zip(carried_a, carried_b):
                            condition_a, branch_a, src_a, _ = branch_a
                            condition_b, branch_b, src_b, _ = branch_b
                            # Sources should differ at least in their address
                            Logger.log_assert(condition_a == condition_b and branch_a == branch_b, f'Unexpected different branches: ({condition_a} {branch_a}), ({condition_b} {branch_b})', addr=location.addr)
                            function_context.aliases[src_b.address] = src_a.address


            # Determine delay of execution packet and consume delay slots
            delay_consumption = 0
            location = ArchAndAddr(arch, ep_location.addr + len(ep_bytes))
            _header_suffix = view.read(location.addr, FP_SIZE - (location.addr % FP_SIZE))
            for instr in arch.disasm.disasm(ep_bytes+_header_suffix, ep_location.addr):
                delay_consumption = max(get_delay_consumption(instr), delay_consumption)
                if (instr.opcode in ('addkpc', 'callp')
                        or any((RW.write in op.access_info.rw 
                            and isinstance(op, RegisterOperand)
                            and op.register == Register.B3
                            for op in instr.operands))):
                    last_return_write = 0
                if not (instr.parallel or instr.is_fp_header()): break
            for _ in range(delay_consumption):
                s.conditions.end_ep()
                if len(pending_branches):
                    branch_slot = pending_branches.pop(0)
                    branch_slot = __unify_branches(branch_slot)
                    for active_branch in branch_slot:
                        carried_branches = __get_carried_branches(active_branch, pending_branches, s.conditions)
                        handle_branch(active_branch, last_return_write <= BRANCH_DELAY,  carried_branches)
            last_return_write += delay_consumption
            
            location = ArchAndAddr(arch, ep_location.addr + len(ep_bytes))

            # update and check termination conditions
            total_size += len(ep_bytes)

            if ends_block: break
            if (max_size and total_size > max_size):
                max_size_reached = True
                break

        if location.addr != block.start:
            # Block has one or more instructions, add it to the function
            __update_context(function_context, location.addr, view)
            block.end = location.addr
            context.add_basic_block(block)
        
        if max_size_reached: break

    if max_size_reached: context.max_size_reached = True
    context.finalize()

def __update_context(context: FunctionContext, end_addr: int, view: BinaryView):
    '''Add next FP header to context if the block ends in the middle of an FP.
    
    *Should be used at the end address of each basic block.*
    '''
    if end_addr % FP_SIZE:
        header_suffix = view.read(end_addr, FP_SIZE - (end_addr % FP_SIZE))
        fp_header = header_suffix[-ARCH_SIZE:]
        if len(header_suffix) >= ARCH_SIZE and fp_header[-1] & 0xf0 == 0xe0:
            fp_addr = end_addr - (end_addr % FP_SIZE)
            context.headers[fp_addr] = fp_header

def __add_branches_to_context(context: FunctionContext, addr: int, branches: PendingBranches):
    '''Store pending branches in context for lifting.
    Branch types are converted to types relevant for lifting.

    *Should be used at the starting address of every block with pending branches.*
    '''
    branch_contexts = list()
    for delay, slot in enumerate(branches):
        for condition, branch, src, _ in slot:
            assert src is not None
            match branch.type:
                case BranchType.CallDestination:
                    branch_type = ILBranchType.Call
                case BranchType.FunctionReturn:
                    branch_type = ILBranchType.Return
                case (BranchType.ExceptionBranch | BranchType.UserDefinedBranch):
                    continue
                case _:
                    # Actual branch type requires additional information
                    branch_type = ILBranchType.UNDETERMINED
            branch_contexts.append(BranchContext(condition, delay, branch_type, src))
    context.branches[addr] = branch_contexts

def __specify_branch_type(context: FunctionContext, block_start: int, branch_type: BranchType, src: Instruction, ends_block: bool, specified_branches: dict[int, list[BranchContext]]):
    '''Specify branch type for lifting in context and collect branches issued in this block.
    
    The specified branches are branches from the current basic block that may be relevant if a block is split up later.
    '''
    if block_start not in context.branches: return
    match branch_type:
        case BranchType.CallDestination:
            if ends_block:
                il_type = ILBranchType.Tailcall
            else:
                il_type = ILBranchType.Call
        case _:
            il_type = ILBranchType.Jump
    for branch_context in context.branches[block_start]:
        if branch_context.src.address != src.address: continue
        if branch_context.type != ILBranchType.UNDETERMINED: break
        branch_context.type = il_type
        break
    else:
        # Branch was issued in the same block, not pending at its beginning.
        # The specified type should still be stored because block splitting
        # could change this later and the analysis only happens once.
        if block_start not in specified_branches:
            specified_branches[block_start] = list()
        specified_branches[block_start].append(BranchContext(src.condition, -1, il_type, src))

def __transfer_specified_branches(context: FunctionContext, specified_branches: list[BranchContext], block_start: int):
    '''Add information on branches from one block to another based on matching targets.
    
    This builds on the assumption that pending branches should be identical for different sources that branch to a basic block.
    In that case, type information from one source also applies to the other.
    Additionally, both sources are connected for lifting (aliases).
    '''
    for branch_context in context.branches[block_start]:
        if branch_context.type != ILBranchType.UNDETERMINED: continue
        target_a = branch_context.src.operands[0]
        for specified_branch in specified_branches:
            target_b = specified_branch.src.operands[0]
            if target_a == target_b:
                branch_context.type = specified_branch.type
                specified_branches.remove(specified_branch)
                context.aliases[branch_context.src.address] = specified_branch.src.address
                break

def __addr_is_executable(view: BinaryView, addr: int) -> bool:
    return view.is_offset_executable(addr)

def __unify_branches(branches: BranchSlot) -> UnifiedSlot:
    '''Convert collected branches for a cycle to a unified branch slot.

    In one cycle, multiple conditional branches may be issued.
    These branches and conditions are collected in a branch slot.
    If there are conditional branches, a single false branch should exist.
    
    There are a number of possible cases with conditional branches:
    1. A single conditional branch exists. There is a fallthrough false branch if this branch is not taken.
    2. Two conditional branches with inverted conditions exist. One of these branches is converted to a false branch. An indirect branch is always classified as true case.
    3. Two conditional branches exist, but their conditions are not inverted. This results in third case as fallthrough false branch.
    '''
    if len(branches) == 0: return list()
    have_sploop = any([b.type == BranchType.UserDefinedBranch for _,b,_,_ in branches])
    Logger.log_assert(len(branches) <= 2 
            or len(branches) == 3 and have_sploop, 'Invalid execution packet')
    unified_branches: UnifiedSlot = list()
    require_fallthrough = False
    conditions = {c for c,_,_,_ in branches}
    have_return = any([b.type == BranchType.FunctionReturn for _,b,_,_ in branches])
    for condition, branch, src, delay in branches:
        edge_type = branch.type
        if branch.type == BranchType.UserDefinedBranch:
            pass # SPLOOP branches have special semantics
        if branch.type == BranchType.FunctionReturn:
            if condition != ConditionType.UNCONDITIONAL and len(branches) == 1:
                require_fallthrough = True
        elif condition != ConditionType.UNCONDITIONAL:
            assert condition != ConditionType.RESERVED
            if ConditionType(condition.value ^ 1) in conditions:
                if have_return:
                    edge_type = branch.type
                elif condition & 1:
                    edge_type = BranchType.FalseBranch
                else:
                    edge_type = BranchType.TrueBranch
            else:
                require_fallthrough = True
                edge_type = BranchType.TrueBranch
        unified_branches.append(AnalyzedBranch(condition, edge_type, branch, src, delay))
    if require_fallthrough:
        if len(conditions) == 1:
            condition = ConditionType(conditions.pop().value ^ 1)
            src = unified_branches[0].instruction
        else:
            # Cannot express negation in one condition
            condition = ConditionType.RESERVED
            src = None
        if have_return:
            edge_type = BranchType.UnconditionalBranch
        else:
            edge_type = BranchType.FalseBranch
        false_branch = InstructionBranch(BranchType.FalseBranch, 0, branch.arch)
        unified_branches.append(AnalyzedBranch(condition, edge_type, false_branch, src, BRANCH_DELAY))
    return unified_branches

def __get_carried_branches(active_branch: AnalyzedBranch, pending_branches: PendingBranches, cond_state: ConditionState) -> PendingBranches:
    '''Get the pending branches carried to a target block for the branch condition.

    Carried branches are pending branches that apply to the target block.
    Because branches may be conditional, this may be a subset of the pending branches.
    For example, if the current branch is `[A0] b`, then other conditions, like `[!A0]`, would not be carried.

    Additionally, we can sometimes generalize the condition of carried branches.
    If the active condition is equal to a pending branch condition, the branch will be unconditional for the target block.
    '''
    active_condition = active_branch.condition
    delay = active_branch.delay
    carried_branches = list()
    for delta, branch_slot in enumerate(pending_branches, start=1):
        carried_branch_slot = list()
        for condition, branch, src, branch_delay in branch_slot:
            if (condition == ConditionType.RESERVED
                    or cond_state.is_impossible(active_condition, delay, condition, delta)):
                continue # do not carry fallthrough and impossible branches
            if (cond_state.is_equivalent(active_condition, delay, condition, delta) or
                    condition == ConditionType.UNCONDITIONAL):
                carried_type = BranchType.UnconditionalBranch if branch.target else BranchType.IndirectBranch
                carried_branch = InstructionBranch(carried_type, branch.target, branch.arch)
                carried_branch_slot.append(QueuedBranch(ConditionType.UNCONDITIONAL, carried_branch, src, branch_delay))
            else:
                carried_branch_slot.append(QueuedBranch(condition, branch, src, branch_delay))
        carried_branches.append(carried_branch_slot)
    while len(carried_branches) and len(carried_branches[-1]) == 0:
        carried_branches.pop()
    return carried_branches

