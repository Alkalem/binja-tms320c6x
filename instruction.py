from binaryninja.architecture import InstructionTextToken, InstructionInfo
from binaryninja.enums import InstructionTextTokenType, BranchType
from binaryninja.log import log_warn

from typing import Any, Generator, Optional
from dataclasses import dataclass

from .constants import ARCH_SIZE, LOAD_BASE
from .disassembler import Disassembler as C6xDisassembler
from .disassembler.types import Operand, Instruction, Register, \
        ImmediateOperand, RegisterOperand, ControlRegisterOperand, \
        RegisterPairOperand, MemoryOperand, FuncUnitsOperand, ISA

@dataclass
class _BranchInfo:
    delay:int
    type:BranchType
    target:int
    conditional:bool

class Disassembler:
    def __init__(self, isa:ISA=ISA.C67X):
        self.__dis = C6xDisassembler(isa=isa)
    
    def disasm(self, data, addr, limit=-1) -> Generator[Instruction, Any, None]:
        return self.__dis.disasm(data, addr, count=limit)

    def decode(self, data, addr) -> Instruction:
        try:
            instr = next(self.__dis.disasm(
                    data, addr, count=1))
        except StopIteration:
            assert False, 'Disassembler should return result'
        return instr
    
    def info(self, data, addr):
        instructions = self.disasm(data, addr)
        instr = next(instructions)
        result = InstructionInfo()
        result.length = instr.size
        if instr.is_invalid() or instr.is_fp_header(): return result
        
        branch_info = self.__get_branch(instr)
        if branch_info is not None:
            # Work around: calculate instruction delay by look-ahead
            # (see binaryninja-api issue 6868)
            branch_delay = branch_info.delay
            instruction_delay = 0
            false_target = addr + instr.size
            current_instr = instr
            while branch_delay > 0 or current_instr.parallel:
                try:
                    current_instr = next(instructions)
                except StopIteration:
                    log_warn('instruction stream did not consume branch delay')
                    break
                instruction_delay += 1
                false_target += current_instr.size
                if current_instr.is_fp_header(): continue
                if current_instr.parallel: 
                    branch_delay += 1
                if current_instr.opcode == 'nop':
                    assert isinstance(current_instr.operands[0], ImmediateOperand)
                    branch_delay -= current_instr.operands[0].value
                else:
                    branch_delay -= 1
            
            result.branch_delay = instruction_delay
            if instr.condition.branch and branch_info.conditional:
                    result.add_branch(BranchType.TrueBranch, branch_info.target)
                    result.add_branch(BranchType.FalseBranch, false_target)
            else:
                result.add_branch(branch_info.type, branch_info.target)
        return result
    
    def __get_branch(self, instr:Instruction) -> Optional[_BranchInfo]:
        match instr.opcode:
            case 'spkernel'|'spkernelr':
                return _BranchInfo(0, BranchType.UserDefinedBranch, 0, False)
            case 'swe'|'swenr':
                return _BranchInfo(0, BranchType.ExceptionBranch, 0, False)
            case 'b'|'bpos'|'bdec':
                delay, conditional = 5, True
            case 'bnop':
                assert isinstance(instr.operands[1], ImmediateOperand)
                delay = max(0, 5-instr.operands[1].value)
                conditional = True
            case'callp':
                delay, conditional = 0, False
            case _: return

        match instr.operands[0]:
            case ImmediateOperand(target):
                return _BranchInfo(delay, BranchType.UnconditionalBranch,
                        target, conditional)
            case RegisterOperand(_)|ControlRegisterOperand(_):
                return _BranchInfo(delay, BranchType.IndirectBranch, 
                        0, conditional)
    

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
        case RegisterOperand(register)|ControlRegisterOperand(register):
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
        case FuncUnitsOperand(units):
            tokens = list()
            unit_list = sorted(units)
            if len(unit_list) > 0:
                tokens.append(InstructionTextToken(
                    InstructionTextTokenType.TextToken, 
                    str(unit_list[0])))
                for unit in unit_list[1:]:
                    tokens.extend([
                        InstructionTextToken(
                            InstructionTextTokenType.TextToken, 
                            str(', ')),
                        InstructionTextToken(
                            InstructionTextTokenType.TextToken, 
                            str(unit))
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
            str(instr.unit))
    )
    middle_length += len(str(instr.unit))
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
