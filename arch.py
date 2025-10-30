from binaryninja.architecture import Architecture, BasicBlockAnalysisContext, RegisterInfo
from binaryninja.basicblock import BasicBlock
from binaryninja.callingconvention import CallingConvention
from binaryninja.function import ArchAndAddr, Function

from queue import SimpleQueue
from typing import Dict, Optional, Set

from .constants import *
from .instruction import Disassembler, gen_tokens, RegisterOperand
from .lifting import lift_il
from .util import get_delay_consumption


class TMS320C67x(Architecture):
    name = 'TMS320C67x'

    address_size = ARCH_SIZE        # 32-bit addresses
    default_int_size = ARCH_SIZE    # 4-byte integers
    instr_alignment = ARCH_SIZE     # fixed 4 byte alignment
    # max_instr_length = ARCH_SIZE  # maximum length
    # Work around: include possible branch delay instructions
    # (see binaryninja-api issue 6868)
    max_instr_length = ARCH_SIZE * (8*(5+1)) 

    regs = {
        name: RegisterInfo(name, ARCH_SIZE)
        for name in REGISTER_NAMES
    } | {
        name+'H': RegisterInfo(name, ARCH_SIZE//2, ARCH_SIZE//2)
        for name in REGISTER_NAMES
    } | {
        name+'L': RegisterInfo(name, 0, ARCH_SIZE//2)
        for name in REGISTER_NAMES
    }

    stack_pointer = 'B15'

    disasm = Disassembler()

    def get_instruction_info(self, data, addr):
        return self.disasm.info(data, addr)
    
    def get_instruction_text(self, data, addr):
        instruction = self.disasm.decode(data, addr)
        if instruction is not None:
            tokens = gen_tokens(instruction)
        else:
            tokens = []
        return tokens, ARCH_SIZE
    
    def get_instruction_low_level_il(self, data, addr, il):
        return lift_il(self.disasm, data, addr, il)
    
    def analyze_basic_blocks(self, func: Function, 
            context: BasicBlockAnalysisContext) -> None:
        #TODO: sound error handling
        data = func.view
        blocks_to_process:SimpleQueue[ArchAndAddr] = SimpleQueue()
        instr_blocks:Dict[ArchAndAddr, BasicBlock] = dict()
        seen_blocks:Set[ArchAndAddr] = set()

        # Start by processing the entry point of the function
        start = func.start
        blocks_to_process.put(ArchAndAddr(self, start))
        seen_blocks.add(ArchAndAddr(self, start))

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
            # For basic block analysis, only delayed branch instructions are relevant.
            delay_slot_count = 0

            # Disassemble the instructions in the block
            ends_block = False
            while True:
                if data.analysis_is_aborted: break

                #TODO: change to max_instr_length when workaround is removed
                # Read a fetch packet and analyze next execution packet in it.
                fetch_packet = data.read(location.addr, ARCH_SIZE * 8)
                if len(fetch_packet) == 0: break
                info = self.get_instruction_info(fetch_packet, location.addr)

                #TODO: split blocks when processing jump into block middle
                #TODO: handle branches

                instr_blocks[location] = block
                execution_packet = fetch_packet[:info.length]
                block.add_instruction_data(execution_packet)

                # Determine delay of execution packet and consume delay slots
                delay_consumption = 0
                for instr in self.disasm.disasm(execution_packet, location.addr):
                    delay_consumption = max(get_delay_consumption(instr), delay_consumption)
                delay_slot_count = max(0, delay_slot_count - delay_consumption)
                
                next_func_addr = data.get_next_function_start_after(location.addr)
                location = ArchAndAddr(self, location.addr + info.length)
                ends_block = next_func_addr <= location.addr
                if ends_block and not delay_slot_count: break

            if location.addr != block.start:
                # Block has one or more instructions, add it to the function
                block.end = location.addr
                context.add_basic_block(block)

        context.finalize()
    

class C67Call(CallingConvention):
    name = 'c67call'

    caller_saved_regs = [
        'A0', 'B0', 'A1', 'B1', 'A2', 'B2', 'A3', 
        'A4', 'B4', 'A5', 'B5', 'A6', 'B6', 'A7', 'B7',
        'A8', 'B8', 'A9', 'B9', 'A10', 'B10', 'A11', 'B11',
        'A12', 'B12', 'A13', 'B13', 'A14', 'B14'
    ]
    callee_saved_regs = [
        'B3', 'A15', 'B15'
    ]
    int_arg_regs = [
        'A4', 'B4', 'A6', 'B6', 'A8', 'B8'
    ]

    eligible_for_heuristics = True
    int_return_reg = 'A4'
    high_int_return_reg = 'A5'

