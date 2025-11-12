from binaryninja.architecture import InstructionTextToken, InstructionInfo
from binaryninja.enums import InstructionTextTokenType, BranchType
from binaryninja.log import log_warn

from typing import Any, Generator, Optional

from .constants import ARCH_SIZE, LOAD_BASE
from .disassembler import Disassembler as C6xDisassembler
from .disassembler.types import Operand, Instruction, Register, \
        ImmediateOperand, RegisterOperand, ControlRegister, \
        RegisterPairOperand, MemoryOperand


class Disassembler:
    def __init__(self):
        self.__dis = C6xDisassembler()
    
    def disasm(self, data, addr, limit=-1) -> Generator[Instruction, Any, None]:
        return self.__dis.disasm(data, addr, count=limit)

    def decode(self, data, addr) -> Optional[Instruction]:
        try:
            instr = next(self.__dis.disasm(
                    data, addr, count=1))
        except StopIteration:
            return None
        return instr
    
    def info(self, data, addr):
        instr = self.decode(data, addr)
        result = InstructionInfo()
        result.length = ARCH_SIZE
        if instr is None: return result
        
        if instr.opcode == 'b':
            # Work around: calculate instruction delay by look-ahead
            # (see binaryninja-api issue 6868)
            branch_delay = 5
            instruction_delay = 0
            if instr.parallel:
                # next instruction is part of current fetch packet 
                branch_delay += 1
            while branch_delay > 0:
                instruction_delay += 1
                delay_instr = self.decode(
                        data[4*instruction_delay:],
                        addr + 4*instruction_delay
                    )
                # log_warn(f"{delay_instr.mnemonic}, {instruction_delay}")
                if delay_instr is None:
                    branch_delay -= 1 # assume not parallel
                elif delay_instr.parallel: 
                    continue
                elif delay_instr.opcode == 'nop':
                    assert isinstance(delay_instr.operands[0], ImmediateOperand)
                    branch_delay -= delay_instr.operands[0].value
                else:
                    branch_delay -= 1
            
            result.branch_delay = instruction_delay
            if (isinstance(instr.operands[0], RegisterOperand)
                    and str(instr.operands[0]) == 'B3'):
                #TODO: this should be a calling convention
                result.add_branch(BranchType.FunctionReturn)
            elif instr.condition.branch:
                if instr.condition.branch == False:
                    result.add_branch(BranchType.FalseBranch)
                    result.add_branch(BranchType.TrueBranch, 
                            addr + (instruction_delay+1)*4)
                else:
                    result.add_branch(BranchType.TrueBranch)
                    result.add_branch(BranchType.FalseBranch, 
                            addr + (instruction_delay+1)*4)
            else:
                #TODO: resolve destinations and fix branch type
                result.add_branch(BranchType.CallDestination)
                # result.add_branch(BranchType.UnresolvedBranch)
        return result
    

def _gen_operand_tokens(operand: Operand):
    match operand:
        case ImmediateOperand(value):
            if abs(value) > 9:
                integer = hex(value)
            else:
                integer = str(value)
            if value >= LOAD_BASE:
                return [InstructionTextToken(
                        InstructionTextTokenType.PossibleAddressToken,
                        integer)]
            else:
                return [InstructionTextToken(
                        InstructionTextTokenType.IntegerToken,
                        integer)]
        case RegisterOperand(register)|ControlRegister(register):
            return [InstructionTextToken(
                    InstructionTextTokenType.RegisterToken,
                    register.name)]
        case RegisterPairOperand(high, low):
            return [
                InstructionTextToken(
                    InstructionTextTokenType.RegisterToken,
                    high.name
                ),
                InstructionTextToken(
                    InstructionTextTokenType.TextToken,
                    ':'
                ),
                InstructionTextToken(
                    InstructionTextTokenType.RegisterToken,
                    low.name
                )
            ]
        case MemoryOperand(mode, base, offset):
            if mode & 2:
                mode_pre = "*"
            elif mode & 9 == 0:
                mode_pre = "*-"
            elif mode & 9 == 1:
                mode_pre = "*+"
            elif mode & 9 == 8:
                mode_pre = "*--"
            else:
                mode_pre = "*++"
            tokens = [
                InstructionTextToken(
                    InstructionTextTokenType.TextToken, 
                    mode_pre),
                InstructionTextToken(
                    InstructionTextTokenType.RegisterToken,
                    base.name
                ),
            ]
            if mode & 2:
                mode_post = "++" if mode & 1 else "--" 
                tokens.append(InstructionTextToken(
                    InstructionTextTokenType.TextToken, 
                    mode_post))
            
            match offset:
                case Register():
                    offset_operand = RegisterOperand(offset)
                case int():
                    offset_operand = ImmediateOperand(offset)

            tokens.extend([
                InstructionTextToken(
                    InstructionTextTokenType.BeginMemoryOperandToken, 
                    "["),
                *_gen_operand_tokens(offset_operand),
                InstructionTextToken(
                    InstructionTextTokenType.EndMemoryOperandToken, 
                    "]")
            ])
            return tokens
        case _:
            raise NotImplementedError(f'operand type {type(operand)}')

def gen_tokens(instr: Instruction, offset: int):
    tokens = list()
    if instr.condition.branch is not None and instr.condition.register:
        tokens.extend([
            InstructionTextToken(
                InstructionTextTokenType.TextToken, 
                '[' if instr.condition.branch else '[!'),
            InstructionTextToken(
                InstructionTextTokenType.RegisterToken,
                instr.condition.register.name
            ),
            InstructionTextToken(
                InstructionTextTokenType.TextToken, "]"),
        ])
    align = 6 if offset else 9
    tokens.append(
        InstructionTextToken(
            InstructionTextTokenType.TextToken, ' ' * (align-len(str(instr.condition)))),   
    )

    tokens.append(
        InstructionTextToken(
            InstructionTextTokenType.InstructionToken, 
            instr.opcode)
    )
    middle_length = len(instr.opcode)
    tokens.append(
        InstructionTextToken(
            InstructionTextTokenType.TextToken, 
            instr.unit)
    )
    middle_length += len(instr.unit)
    tokens.append(
        InstructionTextToken(
            InstructionTextTokenType.TextToken, 
            " " * (12 - middle_length)),
    )

    if len(instr.operands) > 0:
        tokens.extend(_gen_operand_tokens(instr.operands[0]))
        for op in instr.operands[1:]:
            tokens.append(
                InstructionTextToken(
                    InstructionTextTokenType.OperandSeparatorToken, 
                    ", ")
            )
            tokens.extend(_gen_operand_tokens(op))

    tokens.append(
            InstructionTextToken(
                InstructionTextTokenType.NewLineToken, "", 
                offset))
    if instr.parallel:
        tokens.append(
            InstructionTextToken(
                InstructionTextTokenType.TextToken, 
                "|| ")
        )


    return tokens
