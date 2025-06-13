from binaryninja.function import InstructionTextToken, InstructionInfo
from binaryninja.enums import InstructionTextTokenType, BranchType
from binaryninja import log_warn

from dataclasses import dataclass
from typing import Optional, List, Any
from enum import IntEnum

from .constants import ARCH_SIZE, LOAD_BASE, REGISTER_NAMES
from .disassembler import Disassembler as C6xDisassembler
from .disassembler.types import OperandType


class Operand:
    @classmethod
    def from_str(cls, repr:str):
        raise NotImplementedError("abstract method")

    def gen_tokens(self) -> List[InstructionTextToken]:
        raise NotImplementedError("abstract method")
    
    def get_value(self) -> Any:
        return None

class IntegerOperand(Operand):
    def __init__(self, value:int):
        self.value:int = value

    @classmethod
    def from_str(cls, repr:str):
        try:
            value = int(repr, 0)
        except ValueError:
            raise ValueError(
                f"invalid literal for integer operand: '{repr}'")
        return IntegerOperand(value)

    def gen_tokens(self):
        if abs(self.value) > 9:
            integer = hex(self.value)
        else:
            integer = str(self.value)
        if self.value >= LOAD_BASE:
            return [InstructionTextToken(
                InstructionTextTokenType.PossibleAddressToken,
                integer
            )]
        else:
            return [InstructionTextToken(
                    InstructionTextTokenType.IntegerToken,
                    integer
                )]
        
    def get_value(self) -> Any:
        return self.value
    
class RegisterOperand(Operand):
    def __init__(self, name:str):
        self.reg_name:str = name

    @classmethod
    def from_str(cls, repr:str):
        if repr.upper() not in REGISTER_NAMES:
            raise ValueError(
                f"invalid literal for register name: '{repr}'")
        return RegisterOperand(repr.upper())

    def gen_tokens(self):
        return [InstructionTextToken(
                InstructionTextTokenType.RegisterToken,
                self.reg_name
            )]
    
    def get_value(self) -> Any:
        return self.reg_name

class MemoryOperand(Operand):
    class Mode(IntEnum):
        NEG_OFFSET = 0
        POS_OFFSET = 1
        PREDECREMENT = 8
        PREINCREMENT = 9
        POSTDECREMENT = 10
        POSTINCREMENT = 11
    
    def __init__(
            self, base:RegisterOperand, 
            offset: IntegerOperand|RegisterOperand,
            mode:Mode):
        self.base:RegisterOperand = base
        self.offset: IntegerOperand|RegisterOperand = offset
        self.mode:MemoryOperand.Mode = mode

    @classmethod
    def from_str(cls, repr:str):
        if "(" in repr:
            value = int(repr[repr.index("(")+1 : -1], 0) // 4
            offset = IntegerOperand(value)
            remaining = repr[:repr.index("(")]
        elif "[" in repr:
            value = repr[repr.index("[")+1 : -1]
            if value.startswith("0x"):
                offset = IntegerOperand.from_str(value)
            elif "a" in value or "b" in value:
                offset = RegisterOperand.from_str(value)
            else:
                offset = IntegerOperand.from_str(value)
            remaining = repr[:repr.index("[")]
        else:
            offset = IntegerOperand(0)
            remaining = repr
        
        if remaining.endswith("--"):
            mode = MemoryOperand.Mode.POSTDECREMENT
            reg_name = remaining[1:-2]
        elif remaining.endswith("++"):
            mode = MemoryOperand.Mode.POSTINCREMENT
            reg_name = remaining[1:-2]
        elif remaining.startswith("*--"):
            mode = MemoryOperand.Mode.PREDECREMENT
            reg_name = remaining[3:]
        elif remaining.startswith("*++"):
            mode = MemoryOperand.Mode.PREINCREMENT
            reg_name = remaining[3:]
        elif remaining[1] in "+-":
            mode = MemoryOperand.Mode.POS_OFFSET
            reg_name = remaining[2:]
        else:
            mode = MemoryOperand.Mode.POS_OFFSET
            reg_name = remaining[1:]
        base = RegisterOperand.from_str(reg_name)

        return MemoryOperand(base, offset, mode)

    def gen_tokens(self):
        if self.mode & 2:
            mode_pre = "*"
        elif self.mode & 9 == 0:
            mode_pre = "*-"
        elif self.mode & 9 == 1:
            mode_pre = "*+"
        elif self.mode & 9 == 8:
            mode_pre = "*--"
        else:
            mode_pre = "*++"
        tokens = [
            InstructionTextToken(
                InstructionTextTokenType.TextToken, 
                mode_pre),
            *self.base.gen_tokens(),
        ]
        if self.mode & 2:
            mode_post = "++" if self.mode & 1 else "--" 
            tokens.append(InstructionTextToken(
                InstructionTextTokenType.TextToken, 
                mode_post))
            
        tokens.extend([
            InstructionTextToken(
                InstructionTextTokenType.BeginMemoryOperandToken, 
                "["),
            *self.offset.gen_tokens(),
            InstructionTextToken(
                InstructionTextTokenType.EndMemoryOperandToken, 
                "]")
        ])
        return tokens

@dataclass
class Instruction:
    condition: str
    mnemonic: str
    unit: Optional[str]
    ops: List[Operand]
    parallel: bool
    size: int

    @classmethod
    def invalid(cls):
        return Instruction(
            None, "invalid", None, [], False, ARCH_SIZE
        )

class Disassembler:
    def __init__(self):
        self.__dis = C6xDisassembler()
    

    def decode(self, data, addr) -> Instruction:
        try:
            instr = next(self.__dis.disasm(
                    data, addr, count=1))
        except StopIteration:
            return Instruction.invalid()

        ops = list()
        for operand in instr.operands:
            match operand.type:
                case OperandType.CONST:
                    ops.append(IntegerOperand(operand.value))
                case OperandType.REGISTER:
                    ops.append(RegisterOperand(str(operand.value)))
                case OperandType.ADDRESS:
                    ops.append(MemoryOperand(
                        RegisterOperand(str(operand.value[1])),
                        IntegerOperand(operand.value[2] // 4),
                        MemoryOperand.Mode(operand.value[0].value)))
                case _:
                    raise NotImplementedError('operand type not supported')

        return Instruction(
                str(instr.condition), instr.opcode.upper(), instr.unit,
                ops, instr.parallel, ARCH_SIZE
            )
    
    def info(self, data, addr):
        instr = self.decode(data, addr)
        result = InstructionInfo()
        result.length = instr.size
        
        if instr.mnemonic == "B":
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
                if delay_instr.parallel: 
                    continue
                elif delay_instr.mnemonic == "NOP":
                    branch_delay -= delay_instr.ops[0].get_value()
                else:
                    branch_delay -= 1
            
            result.branch_delay = instruction_delay
            if (isinstance(instr.ops[0], RegisterOperand)
                    and instr.ops[0].reg_name == "B3"):
                #TODO: this should be a calling convention
                result.add_branch(BranchType.FunctionReturn)
            elif instr.condition:
                if "!" in instr.condition:
                    result.add_branch(BranchType.FalseBranch)
                    result.add_branch(BranchType.TrueBranch, 
                            addr+ (instruction_delay+1)*4)
                else:
                    result.add_branch(BranchType.TrueBranch)
                    result.add_branch(BranchType.FalseBranch, 
                            addr + (instruction_delay+1)*4)
            else:
                #TODO: resolve destinations and fix branch type
                result.add_branch(BranchType.CallDestination)
                # result.add_branch(BranchType.UnresolvedBranch)
        return result
    

def gen_tokens(instr: Instruction):
    tokens = list()
    if instr.condition:
        tokens.extend([
            InstructionTextToken(
                InstructionTextTokenType.TextToken, 
                instr.condition.rstrip("AB012]")),
            *RegisterOperand
                    .from_str(instr.condition.strip("[!]"))
                    .gen_tokens(),
            InstructionTextToken(
                InstructionTextTokenType.TextToken, "]"),
        ])
    tokens.append(
        InstructionTextToken(
            InstructionTextTokenType.TextToken, " " * (6-len(instr.condition))),   
    )

    tokens.append(
        InstructionTextToken(
            InstructionTextTokenType.InstructionToken, 
            instr.mnemonic)
    )
    middle_length = len(instr.mnemonic)
    if instr.unit:
        tokens.append(
            InstructionTextToken(
                InstructionTextTokenType.TextToken, 
                instr.unit)
        )
        middle_length += 1 + len(instr.unit)
    tokens.append(
        InstructionTextToken(
            InstructionTextTokenType.TextToken, 
            " " * (12 - middle_length)),
    )

    if len(instr.ops) > 0:
        tokens.extend(instr.ops[0].gen_tokens())
        for op in instr.ops[1:]:
            tokens.append(
                InstructionTextToken(
                    InstructionTextTokenType.OperandSeparatorToken, 
                    ", ")
            )
            tokens.extend(op.gen_tokens())

    if instr.parallel:
        tokens.append(
            InstructionTextToken(
                InstructionTextTokenType.TextToken, 
                " ||")
        )

    return tokens
