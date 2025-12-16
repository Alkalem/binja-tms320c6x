from binaryninja.architecture import BasicBlockAnalysisContext, InstructionBranch
from binaryninja.basicblock import BasicBlock
from binaryninja.binaryview import BinaryView
from binaryninja.enums import BranchType
from binaryninja.function import ArchAndAddr, Function
from binaryninja.lowlevelil import LowLevelILFunction
from binaryninja.log import log_info


from queue import SimpleQueue
from typing import Dict, Optional, Set

from .disassembler.types import ConditionType, Instruction, OperandType, RegisterOperand, Register, ControlRegisterOperand, ControlRegister
from .constants import ARCH_SIZE, FP_SIZE
from .util import get_delay_consumption


def analyze_basic_blocks(arch, func: Function, 
        context: BasicBlockAnalysisContext) -> None:
    #TODO: sound error handling
    view = func.view
    blocks_to_process:SimpleQueue[ArchAndAddr] = SimpleQueue()
    instr_blocks:Dict[ArchAndAddr, BasicBlock] = dict()
    seen_blocks:Set[ArchAndAddr] = set()

    # Start by processing the entry point of the function
    start = func.start
    blocks_to_process.put(ArchAndAddr(arch, start))
    seen_blocks.add(ArchAndAddr(arch, start))

    while not blocks_to_process.empty():
        if view.analysis_is_aborted: return

        # Get the next block to process
        location = blocks_to_process.get()
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
        delay_slot_count = 0
        pending_branches = list()

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

                    target_block.fallthrough_to_function = False
                    target_block.has_undetermined_outgoing_edges = False
                    target_block.can_exit = True
                    target_block.end = location.addr

                    for e in target_block.get_pending_outgoing_edges():
                        split_block.add_pending_outgoing_edge(e.type, e.target, e.arch, e.fallthrough)
                    target_block.clear_pending_outgoing_edges()
                    target_block.add_pending_outgoing_edge(BranchType.UnconditionalBranch, location.addr, arch, True)

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
                    (location.addr + instr.size + ARCH_SIZE) % FP_SIZE == 0)
                if (not(is_parallel or header_next) or ends_block): break
            block.add_instruction_data(execution_packet)
            if len(new_branches):
                for delay, condition, branch in new_branches:
                    while len(pending_branches) <= delay:
                        pending_branches.append(list())
                    pending_branches[delay].append((condition, branch))

            #TODO: handle branches
            def handle_branch(branch:InstructionBranch):
                nonlocal ends_block
                match branch.type:
                    case BranchType.UnconditionalBranch|BranchType.TrueBranch:
                        ends_block = True
                        if branch.target == 0: return
                        assert branch.target
                        block.add_pending_outgoing_edge(branch.type, branch.target, arch)
                        # log_info(f"Unconditional @{location.addr:08x} to {branch.target:08x}")
                    case BranchType.IndirectBranch:
                        ends_block = True
                    case BranchType.FalseBranch:
                        if branch.target == 0:
                            # fallthrough false condition
                            block.add_pending_outgoing_edge(BranchType.FalseBranch, location.addr, arch)
                        else:
                            ends_block = True
                            block.add_pending_outgoing_edge(BranchType.FalseBranch, branch.target, arch)
                    case BranchType.FunctionReturn:
                        ends_block = True

            # Determine delay of execution packet and consume delay slots
            delay_consumption = 0
            location = ArchAndAddr(arch, ep_location.addr + len(execution_packet))
            _header_suffix = view.read(location.addr, FP_SIZE - (location.addr % FP_SIZE))
            for instr in arch.disasm.disasm(execution_packet+_header_suffix, ep_location.addr):
                delay_consumption = max(get_delay_consumption(instr), delay_consumption)
                if not (instr.parallel or instr.is_fp_header()): break
            for _ in range(delay_consumption):
                if len(pending_branches):
                    branches = pending_branches.pop(0)
                    branches = __unify_branches(branches)
                    for _, branch in branches:
                        handle_branch(branch)
            delay_slot_count = max(0, delay_slot_count - delay_consumption)
            
            location = ArchAndAddr(arch, ep_location.addr + len(execution_packet))
            if ends_block and not delay_slot_count: break

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
            conditions.add(condition)
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
    return branches
