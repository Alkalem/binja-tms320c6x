from .disassembler.types import Instruction, ImmediateOperand

def get_delay_consumption(instr:Instruction):
    delay_slots = 1
    if instr.opcode == 'nop':
        assert isinstance(instr.operands[0], ImmediateOperand)
        delay_slots = instr.operands[0].value
    elif instr.opcode == 'idle':
        # in theory unlimited, but binja limits delay to 255
        delay_slots = 255
    return delay_slots
