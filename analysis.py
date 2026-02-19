from binaryninja.architecture import BasicBlockAnalysisContext, InstructionBranch
from binaryninja.basicblock import BasicBlock
from binaryninja.binaryview import BinaryView
from binaryninja.enums import BranchType, FunctionAnalysisSkipOverride
from binaryninja.function import ArchAndAddr, Function
from binaryninja.lowlevelil import LowLevelILFunction
from binaryninja.log import log_info, log_debug


from dataclasses import dataclass
from typing import Dict, Optional, Set

from .disassembler.types import ConditionType, Instruction, OperandType, RegisterOperand, Register, ControlRegisterOperand, ControlRegister, RW
from .constants import ARCH_SIZE, FP_SIZE, HW_SIZE, BRANCH_DELAY
from .util import get_delay_consumption


@dataclass
class SploopContext:
    active:bool = False
    sploop:Optional[Instruction] = None
    start:int = 0


    def process(self, i:Instruction):
        if i.opcode.startswith('sploop'):
            assert not self.active
            self.active = True
            self.sploop = i
        if self.active and self.start == 0 and not i.parallel:
            self.start = i.address + i.size


def analyze_basic_blocks(arch, func: Function, 
        context: BasicBlockAnalysisContext) -> None:
    #TODO: sound error handling
    view = func.view
    blocks_to_process:list[ArchAndAddr] = list()
    instr_blocks:Dict[ArchAndAddr, BasicBlock] = dict()
    seen_blocks:Set[ArchAndAddr] = set()
    block_carried_branches = dict()
    sploop_blocks = dict()

    # Start by processing the entry point of the function
    start = func.start
    blocks_to_process.append(ArchAndAddr(arch, start))
    seen_blocks.add(ArchAndAddr(arch, start))

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
        # if location in seen_blocks:
        #     continue
        # seen_blocks.add(ArchAndAddr(arch, location.addr))

        # Create a new basic block
        block:BasicBlock = context.create_basic_block(location.arch, location.addr) # type: ignore
        assert block is not None

        # This architecture interpretes a delay slot as a cycle.
        # Due to parallelism and idling instructions,
        # the number of instructions per delay cycle may vary.
        # For basic block analysis, delay is only relevant for branch instructions.
        pending_branches = list()
        if location in block_carried_branches:
            pending_branches = block_carried_branches[location]
        last_return_write = 255
        sploop_context = SploopContext()
        if location in sploop_blocks:
            sploop_context = sploop_blocks[location]

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

                    for addr in range(location.addr, split_block.end, HW_SIZE):
                        k = ArchAndAddr(arch, addr)
                        if k in instr_blocks:
                            instr_blocks[k] = split_block

                    for e in target_block.get_pending_outgoing_edges():
                        split_block.add_pending_outgoing_edge(e.type, e.target, e.arch, e.fallthrough)
                    target_block.clear_pending_outgoing_edges()
                    target_block.add_pending_outgoing_edge(BranchType.UnconditionalBranch, location.addr, arch, True)

                    #TODO: check for pending branches at split point

                    seen_blocks.add(location)
                    context.add_basic_block(split_block)
                    block.add_pending_outgoing_edge(BranchType.UnconditionalBranch, location.addr, arch)
                    break

            #TODO: change reads to max_instr_length when workaround is removed

            # Build execution packet by reading parallel instructions until end of fetch packet.
            execution_packet = b""
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
                execution_packet += instr_bytes[:info.length]
                instr = arch.disasm.decode(instr_bytes, location.addr)
                if instr.is_invalid(): break # error case
                if not instr.is_fp_header():
                    is_parallel = instr.parallel

                sploop_context.process(instr)
                for branch in info.branches:
                    branch = __resolve_branch(branch, instr)
                    new_branch = (info.branch_delay, instr.condition, branch)
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
            block.add_instruction_data(execution_packet)
            if len(new_branches):
                for delay, condition, branch in new_branches:
                    while len(pending_branches) <= delay:
                        pending_branches.append(list())
                    pending_branches[delay].append((condition, branch))

            #TODO: handle function branches and branches with pending delay
            def handle_branch(branch:InstructionBranch, returns:bool, carried_branches):
                log_debug(f'Handling {branch.type.name} @{location.addr:08x} to {branch.target:08x} (return? {returns})')
                nonlocal ends_block

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
                            block.add_pending_outgoing_edge(BranchType.CallDestination, branch.target, arch)
                            ends_block = False
                        else:
                            block.add_pending_outgoing_edge(branch.type, branch.target, arch)
                            add_target_to_process(branch.target, carried_branches)
                    case BranchType.IndirectBranch:
                        ends_block = True
                        target_type = branch.type
                        if is_likely_call(branch, carried_branches, returns):
                            assert len(carried_branches) == 0
                            ends_block = False
                        for indirect_branch in context.indirect_branches:
                            if (indirect_branch.source_addr != location.addr):
                                continue
                            block.add_pending_outgoing_edge(target_type, indirect_branch.dest_addr, arch)
                            add_target_to_process(indirect_branch.dest_addr, carried_branches)
                    case BranchType.FalseBranch:
                        if branch.target == 0:
                            # fallthrough false condition
                            ends_block = True
                            target = location.addr
                            block.add_pending_outgoing_edge(BranchType.FalseBranch, target, arch, True)
                            if sploop_context.active:
                                sploop_blocks[location] = sploop_context
                        else:
                            ends_block = True
                            target = branch.target
                            block.add_pending_outgoing_edge(BranchType.FalseBranch, target, arch)
                        add_target_to_process(target, carried_branches)
                    case BranchType.FunctionReturn:
                        ends_block = True
                    case BranchType.UserDefinedBranch:
                        ends_block = True
                        if sploop_context.sploop is None:
                            sploop_context.start = block.start
                        # used for SPLOOP exit branches
                        block.add_pending_outgoing_edge(
                            BranchType.TrueBranch,
                            sploop_context.start,
                            arch)
                        block.add_pending_outgoing_edge(
                            BranchType.FalseBranch,
                            location.addr,
                            arch)
                        add_target_to_process(sploop_context.start, carried_branches)
                        add_target_to_process(location.addr, carried_branches)
                        sploop_context.active = False
                        sploop_context.start = 0
            
            def is_likely_call(branch:InstructionBranch, carried_branches,
                    returns:bool) -> bool:
                # This address is not helpful if symbols for basic blocks exist
                # next_func_addr = view.get_next_function_start_after(location.addr)
                is_in_function = func.lowest_address <= branch.target
                return (len(carried_branches) == 0 and 
                    (not is_in_function or returns))

            def add_target_to_process(addr:int, carried_branches):
                target = ArchAndAddr(arch, addr)
                if target not in seen_blocks:
                    blocks_to_process.append(target)
                    seen_blocks.add(target)
                if target not in block_carried_branches:
                    block_carried_branches[target] = carried_branches
                else:
                    assert block_carried_branches[target] == carried_branches


            # Determine delay of execution packet and consume delay slots
            delay_consumption = 0
            location = ArchAndAddr(arch, ep_location.addr + len(execution_packet))
            _header_suffix = view.read(location.addr, FP_SIZE - (location.addr % FP_SIZE))
            for instr in arch.disasm.disasm(execution_packet+_header_suffix, ep_location.addr):
                delay_consumption = max(get_delay_consumption(instr), delay_consumption)
                if (instr.opcode in ('addkpc', 'callp')
                        or any((RW.write in op.access_info.rw 
                            and isinstance(op, RegisterOperand)
                            and op.register == Register.B3
                            for op in instr.operands))):
                    last_return_write = 0
                if not (instr.parallel or instr.is_fp_header()): break
            for _ in range(delay_consumption):
                if len(pending_branches):
                    branch_slot = pending_branches.pop(0)
                    branch_slot = __unify_branches(branch_slot)
                    for condition, branch in branch_slot:
                        carried_branches = __get_carried_branches(condition, pending_branches)
                        handle_branch(branch, last_return_write <= BRANCH_DELAY, carried_branches)
            last_return_write += delay_consumption
            
            location = ArchAndAddr(arch, ep_location.addr + len(execution_packet))

            # update and check termination conditions
            total_size += len(execution_packet)

            if ends_block: break
            if (max_size and total_size > max_size):
                max_size_reached = True
                break

        if location.addr != block.start:
            # Block has one or more instructions, add it to the function
            if location.addr % FP_SIZE:
                # Add Header as Workaround (see https://github.com/Vector35/binaryninja-api/issues/742)
                header_suffix = view.read(location.addr, FP_SIZE - (location.addr % FP_SIZE))
                fp_header = header_suffix[-ARCH_SIZE:]
                block.add_instruction_data(fp_header)
                block.end = location.addr # + ARCH_SIZE
            else:
                block.end = location.addr
            context.add_basic_block(block)
        
        if max_size_reached: break

    if max_size_reached: context.max_size_reached = True
    context.finalize()

def __addr_is_executable(view:BinaryView, addr:int) -> bool:
    return view.is_offset_executable(addr)

def __resolve_branch(branch:InstructionBranch, instr:Instruction) -> InstructionBranch:
    match branch.type:
        case BranchType.IndirectBranch:
            # indirect target using register
            assert (isinstance(instr.operands[0], RegisterOperand)
                    or isinstance(instr.operands[0], ControlRegisterOperand))
            if instr.operands[0].register in (
                    Register.B3, ControlRegister.IRP, ControlRegister.NRP):
                # usually used as return address
                branch = InstructionBranch(BranchType.FunctionReturn, branch.target, branch.arch)
    return branch

def __unify_branches(branches):
    if len(branches) == 0: return branches
    unified_branches = list()
    require_false_branch = True
    conditions = set()
    for condition, branch in branches:
        if branch.type == BranchType.TrueBranch:
            if ConditionType(condition.value ^ 1) in conditions:
                require_false_branch = False
                if branch.target == 0:
                    # only one indirect branch per EP possible
                    for c,b in unified_branches:
                        if c == ConditionType(condition.value ^ 1):
                            b.type = BranchType.FalseBranch
                            break
                else:
                    branch = InstructionBranch(BranchType.FalseBranch, branch.target, branch.arch)
            conditions.add(condition)
        elif branch.type == BranchType.FalseBranch:
            continue
        else:
            require_false_branch = False
        unified_branches.append((condition, branch))
    if require_false_branch:
        if len(conditions) == 1:
            condition = ConditionType(conditions.pop().value ^ 1)
        else:
            # Cannot express negation in one condition
            condition = ConditionType.RESERVED
        false_branch = InstructionBranch(BranchType.FalseBranch, 0, branch.arch)
        unified_branches.append((condition, false_branch))
    return unified_branches

def __get_carried_branches(active_condition:ConditionType, pending_branches):
    carried_branches = list()
    for branch_slot in pending_branches:
        carried_branch_slot = list()
        for condition, branch in branch_slot:
            if (branch.type == BranchType.FalseBranch
                    or condition == ConditionType.RESERVED):
                continue # only carry true case
            if (condition == active_condition or
                    condition == ConditionType.UNCONDITIONAL):
                carried_type = BranchType.UnconditionalBranch if branch.target else BranchType.IndirectBranch
                carried_branch = InstructionBranch(carried_type, branch.target, branch.arch)
                carried_branch_slot.append((ConditionType.UNCONDITIONAL, carried_branch))
        carried_branches.append(carried_branch_slot)
    while len(carried_branches) and len(carried_branches[-1]) == 0:
        carried_branches.pop()
    return carried_branches

