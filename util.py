from .disassembler.types import Instruction, ImmediateOperand

def get_delay_consumption(instr:Instruction):
    delay_slots = 1
    if instr.is_fp_header():
        delay_slots = 0
    elif instr.opcode == 'nop':
        assert isinstance(instr.operands[0], ImmediateOperand)
        delay_slots = instr.operands[0].value
    elif instr.opcode == 'idle':
        # in theory unlimited, but binja limits delay to 255
        delay_slots = 255
    elif instr.opcode == 'addkpc':
        assert isinstance(instr.operands[2], ImmediateOperand)
        delay_slots = instr.operands[2].value + 1
    elif instr.opcode == 'bnop':
        assert isinstance(instr.operands[1], ImmediateOperand)
        delay_slots = instr.operands[1].value + 1
    elif instr.opcode == 'callp':
        delay_slots += 5 # implied NOP cycles after instruction
    elif (instr.opcode.startswith('ld')
            and instr.header is not None
            and instr.header.protected_loads):
        delay_slots += 4 # NOP cycles after instruction
    return delay_slots
