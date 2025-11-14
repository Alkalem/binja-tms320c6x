from binaryninja.architecture import Architecture, BasicBlockAnalysisContext
from binaryninja.basicblock import BasicBlock
from binaryninja.function import ArchAndAddr, Function

from queue import SimpleQueue
from typing import Dict, Optional, Set

from .util import get_delay_consumption


def analyze_basic_blocks(arch:Architecture, func: Function, 
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

            # Disassemble the instructions in the block
            ends_block = False
            while True:
                if data.analysis_is_aborted: break

                #TODO: change to max_instr_length when workaround is removed
                # Read a fetch packet and analyze next execution packet in it.
                fetch_packet = data.read(location.addr, ARCH_SIZE * 8)
                if len(fetch_packet) == 0: break
                info = arch.get_instruction_info(fetch_packet, location.addr)

                #TODO: split blocks when processing jump into block middle
                #TODO: handle branches

                instr_blocks[location] = block
                execution_packet = fetch_packet[:info.length]
                block.add_instruction_data(execution_packet)

                # Determine delay of execution packet and consume delay slots
                delay_consumption = 0
                for instr in arch.disasm.disasm(execution_packet, location.addr):
                    delay_consumption = max(get_delay_consumption(instr), delay_consumption)
                delay_slot_count = max(0, delay_slot_count - delay_consumption)
                
                next_func_addr = data.get_next_function_start_after(location.addr)
                location = ArchAndAddr(arch, location.addr + info.length)
                ends_block = next_func_addr <= location.addr
                if ends_block and not delay_slot_count: break

            if location.addr != block.start:
                # Block has one or more instructions, add it to the function
                block.end = location.addr
                context.add_basic_block(block)

        context.finalize()
