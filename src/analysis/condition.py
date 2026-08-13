# Copyright 2026 Benedikt Waibel
# 
# This file is part of the binary ninja tms320c6x architecture plugin.
# 
# This plugin is free software: 
# you can redistribute it and/or modify it under the terms of the GNU General
# Public License as published by the Free Software Foundation, either version 3
# of the License, or (at your option) any later version.
# 
# This program is distributed in the hope that it will be useful,but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License along with
# this program. If not, see <http://www.gnu.org/licenses/>.

from tms320c6x_disassembler.types import ConditionType, Instruction, RW, RegisterOperand, RegisterPairOperand, Register, Operand, ImmediateOperand

from ..util import op_get_high_register, op_get_register, op_get_value


CONDITION_REGISTERS = {
    Register.A0: (ConditionType.A0, ConditionType.NOT_A0),
    Register.A1: (ConditionType.A1, ConditionType.NOT_A1),
    Register.A2: (ConditionType.A2, ConditionType.NOT_A2),
    Register.B0: (ConditionType.B0, ConditionType.NOT_B0),
    Register.B1: (ConditionType.B1, ConditionType.NOT_B1),
    Register.B1: (ConditionType.B2, ConditionType.NOT_B2),
}

class ConditionState:
    def __init__(self) -> None:
        self.moves: list[dict[ConditionType, set[ConditionType]]] = list()
        self.queued_discards: list[list[tuple[ConditionType, ConditionType]]] = list()
        self.lookup = dict()
        for c in ConditionType:
            equivalence_class = {c,}
            self.lookup[c] = equivalence_class
        self.__init_moves()

    def __init_moves(self):
        self.current_moves: dict[ConditionType, set[ConditionType]] = dict()
        for c in ConditionType:
            self.current_moves[c] = {c}
        if len(self.queued_discards):
            for a, b in self.queued_discards.pop(0):
                self.current_moves[a].discard(b)

    def __queue_discard(self, delay: int, src: ConditionType, dst: ConditionType):
        while len(self.queued_discards) < delay:
            self.queued_discards.append(list())
        self.queued_discards[delay-1].append((src, dst))

    def process(self, i: Instruction):
        # TODO: moves should always group conditions
        if i.condition == ConditionType.UNCONDITIONAL and is_cond_move(i):
            match i.operands[-2]:
                case RegisterOperand(src) | RegisterPairOperand(src, _):
                    dst = op_get_register(i.operands[-1])
                    for a, b in zip(CONDITION_REGISTERS[src], CONDITION_REGISTERS[dst]):
                        self.current_moves[a].add(b)
            if isinstance(i.operands[-2], RegisterPairOperand):
                src = op_get_high_register(i.operands[-2])
                dst = op_get_high_register(i.operands[-1])
                if (src in CONDITION_REGISTERS and dst in CONDITION_REGISTERS):
                    for a, b in zip(CONDITION_REGISTERS[src], CONDITION_REGISTERS[dst]):
                        self.current_moves[a].add(b)
        elif is_cond_set(i):
            if i.condition == ConditionType.UNCONDITIONAL:
                pass # TODO: target is always true or always false
            else:
                src = i.condition
                dst_index = 0 if op_get_value(i.operands[0]) else 1
                dst = CONDITION_REGISTERS[op_get_register(i.operands[-1])][dst_index]
                self.current_moves[src].add(dst)
        else:
        # TODO: unknown writes should always break up groups
            for operand in i.operands:
                # Check if condition register is written to
                written_registers = list()
                if operand.access_info.rw in (RW.read_write, RW.write):
                    match operand:
                        case RegisterOperand(r) | RegisterPairOperand(r, _):
                            if r not in CONDITION_REGISTERS:
                                continue
                            written_registers.append(r)
                        case _:
                            continue
                    match operand:
                        case RegisterPairOperand(_, h): 
                            if h in CONDITION_REGISTERS:
                                written_registers.append(h)
                else: continue

                # TODO: fix cycle for high writes
                if operand.access_info.low_first > 1: 
                    delay = operand.access_info.low_first
                    for r in written_registers:
                        for c in CONDITION_REGISTERS[r]:
                            self.__queue_discard(delay, c, c)
                for r in written_registers:
                    for c in CONDITION_REGISTERS[r]:
                        self.current_moves[c].discard(c)

    def end_ep(self):
        self.moves.append(self.current_moves)
        self.__init_moves()

    def __explore_conditions(self, start: set[ConditionType], delay: int, delta: int) -> set[ConditionType]:
        equivalent_conditions = start
        if delay == 0: return equivalent_conditions
        for moves in self.moves[-delay-1: -delay-1+delta]:
            next_equivalent = set()
            for c in equivalent_conditions:
                next_equivalent.update(moves[c])
            equivalent_conditions = next_equivalent
        return equivalent_conditions

    def is_equivalent(self, src: ConditionType, delay: int, dst: ConditionType, delta: int) -> bool:
        equivalent_conditions = self.__explore_conditions({src}, delay, delta)
        negated_conditions = self.__explore_conditions(
                        {ConditionType(src.value ^ 1)}, delay, delta)
        return (dst in equivalent_conditions
                or ConditionType(dst.value ^ 1) in negated_conditions)

    def is_impossible(self, src: ConditionType, delay: int, dst: ConditionType, delta: int) -> bool:
        impossible_conditions = self.__explore_conditions(
                {ConditionType(src.value ^ 1)}, delay, delta)
        equivalent_conditions = self.__explore_conditions({src}, delay, delta)
        return (dst in impossible_conditions
                or ConditionType(dst.value ^ 1) in equivalent_conditions)

### Condition util ###

def is_cond_move(i: Instruction) -> bool:
    match i.opcode:
        case 'mv':
            if (op_get_register(i.operands[0]) in CONDITION_REGISTERS
                    and op_get_register(i.operands[1]) in CONDITION_REGISTERS):
                return True
        case 'or' | 'and':
            if (isinstance(i.operands[0], ImmediateOperand)
                    and op_get_value(i.operands[0]) == 0
                    and op_get_register(i.operands[1]) in CONDITION_REGISTERS
                    and op_get_register(i.operands[2]) in CONDITION_REGISTERS):
                return True
    return False

def is_cond_set(i: Instruction) -> bool:
    match i.opcode:
        case 'mvk':
            if (op_get_register(i.operands[-1]) in CONDITION_REGISTERS):
                return True
    return False


