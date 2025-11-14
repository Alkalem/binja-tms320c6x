from binaryninja.function import InstructionTextToken, InstructionInfo
from binaryninja.enums import InstructionTextTokenType, BranchType
from binaryninja import log_warn

from dataclasses import dataclass
from typing import Optional, List, Any, Generator

from .constants import ARCH_SIZE, LOAD_BASE
from .disassembler import Disassembler as C6xDisassembler
from .disassembler.types import Operand, Instruction, Register, \
        ImmediateOperand, RegisterOperand, ControlRegister, \
        RegisterPairOperand, MemoryOperand, ControlRegisterOperand


class Disassembler:
    def __init__(self):
        self.__dis = C6xDisassembler()
    
    def disasm(self, data, addr) -> Generator[Instruction, Any, None]:
        return self.__dis.disasm(data, addr)

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
            result.branch_delay = 5
            target = 0
            match instr.operands[0]:
                case RegisterOperand(Register.B3):
                    #TODO: this should be a calling convention
                    branch_type = BranchType.FunctionReturn
                case RegisterOperand(_):
                    branch_type = BranchType.IndirectBranch
                case (ControlRegisterOperand(ControlRegister.IRP) |
                        ControlRegisterOperand(ControlRegister.NRP)):
                    # Interrupt service routines end similar to functions
                    branch_type = BranchType.FunctionReturn
                case op:
                    assert type(op) == ImmediateOperand, f'Unexpected branch target {op}'
                    branch_type = BranchType.UnconditionalBranch
                    fp_address = addr - (addr % (8*ARCH_SIZE))
                    target = fp_address + (op.value << 2)
            if instr.condition.branch is not None:
                match branch_type:
                    case BranchType.FunctionReturn:
                        result.add_branch(branch_type, target)
                    case _:
                        result.add_branch(BranchType.TrueBranch, target)
                result.add_branch(BranchType.FalseBranch)
            else:
                result.add_branch(branch_type, target)
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

def gen_tokens(instr: Instruction):
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
    tokens.append(
        InstructionTextToken(
            InstructionTextTokenType.TextToken, ' ' * (6-len(str(instr.condition)))),   
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

    if instr.parallel:
        tokens.append(
            InstructionTextToken(
                InstructionTextTokenType.TextToken, 
                " ||")
        )

    return tokens
