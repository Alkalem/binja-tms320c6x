from binaryninja.architecture import BasicBlockAnalysisContext
from binaryninja.basicblock import BasicBlock
from binaryninja.enums import BranchType
from binaryninja.function import ArchAndAddr, Function
from binaryninja.lowlevelil import LowLevelILFunction
from binaryninja import log_info


from queue import SimpleQueue
from typing import Dict, Optional, Set

from .constants import ARCH_SIZE
from .util import get_delay_consumption


def analyze_basic_blocks(arch, func: Function, 
            context: BasicBlockAnalysisContext) -> None:
        #TODO: sound error handling
        data = func.view
        blocks_to_process:SimpleQueue[ArchAndAddr] = SimpleQueue()
        instr_blocks:Dict[ArchAndAddr, BasicBlock] = dict()
        seen_blocks:Set[ArchAndAddr] = set()

        # Start by processing the entry point of the function
        start = func.start
        blocks_to_process.put(ArchAndAddr(arch, start))
        seen_blocks.add(ArchAndAddr(arch, start))

        while not blocks_to_process.empty():
            if data.analysis_is_aborted: return

            # Get the next block to process
            location = blocks_to_process.get()

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
                if data.analysis_is_aborted: break
                #TODO: split blocks when processing jump into block middle

                #TODO: change reads to max_instr_length when workaround is removed

                # Build execution packet by reading parallel instructions until end of fetch packet.
                execution_packet = b""
                ep_location = location
                new_branches = list()
                while True:
                    instr_bytes = data.read(location.addr, ARCH_SIZE)
                    if len(instr_bytes) == 0:
                        ends_block = True
                        break

                    info = arch.get_instruction_info(instr_bytes, location.addr)

                    instr_blocks[location] = block
                    execution_packet += instr_bytes
                    instr = arch.disasm.decode(execution_packet, location.addr)
                    if instr is None: break # error case

                    for branch in info.branches:
                        new_branch = (instr.condition, branch)
                        new_branches.append(new_branch)

                    next_func_addr = data.get_next_function_start_after(location.addr)
                    location = ArchAndAddr(arch, location.addr + info.length)
                    # ends_block |= next_func_addr <= location.addr
                    #TODO: fall through to next function?
                    if (not instr.parallel or ends_block
                        or (location.addr % (ARCH_SIZE * 8) == 0)): break
                block.add_instruction_data(execution_packet)
                #TODO: handle dual jumps
                if len(new_branches):
                    while len(pending_branches) < info.branch_delay:
                        pending_branches.append(list())
                    pending_branches.append(new_branches)

                #TODO: handle branches
                def handle_branch(branch):
                    nonlocal ends_block
                    match branch.type:
                        case BranchType.FunctionReturn:
                            ends_block = True

                # Determine delay of execution packet and consume delay slots
                delay_consumption = 0
                for instr in arch.disasm.disasm(execution_packet, ep_location.addr):
                    delay_consumption = max(get_delay_consumption(instr), delay_consumption)
                if delay_consumption and len(pending_branches):
                    for _ in range(delay_consumption):
                        branches = pending_branches.pop(0)
                        for _, branch in branches:
                            handle_branch(branch)
                delay_slot_count = max(0, delay_slot_count - delay_consumption)
                
                location = ArchAndAddr(arch, ep_location.addr + len(execution_packet))
                if ends_block and not delay_slot_count: break

            if location.addr != block.start:
                # Block has one or more instructions, add it to the function
                block.end = location.addr
                context.add_basic_block(block)

        context.finalize()
