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

from binaryninja.log import log_info

from tms320c6x_disassembler.types import ConditionType, Instruction, RW, RegisterOperand, RegisterPairOperand, Register, Operand, ImmediateOperand

from ..util import unwrap


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
        self.moves: list[dict[ConditionType, list[ConditionType]]] = list()
        self.lookup = dict()
        for c in ConditionType:
            equivalence_class = {c,}
            self.lookup[c] = equivalence_class
        self.__init_moves()

    def __init_moves(self):
        self.current_moves: dict[ConditionType, list[ConditionType]] = dict()
        for c in ConditionType:
            self.current_moves[c] = [c]

    def process(self, i: Instruction):
        # TODO: moves should group conditions
        if i.condition == ConditionType.UNCONDITIONAL and is_cond_move(i):
            match i.operands[-2]:
                case RegisterOperand(src):
                    dst = unwrap(_get_register(i.operands[-1]))
                    for a, b in zip(CONDITION_REGISTERS[src], CONDITION_REGISTERS[dst]):
                        self.current_moves[a].append(b)
        elif is_cond_set(i):
            if i.condition == ConditionType.UNCONDITIONAL:
                pass # TODO: target is always true or always false
            else:
                src = i.condition
                dst_index = 0 if _get_value(i.operands[0]) else 1
                dst = CONDITION_REGISTERS[_get_register(i.operands[-1])][dst_index]
                self.current_moves[src].append(dst)
        else:
        # TODO: unknown writes should break up groups
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
                        case RegisterPairOperand(_, h): written_registers.append(h)
                else: continue

                # TODO: take delay into account
                if operand.access_info.low_last > 1: continue
                for r in written_registers:
                    for c in CONDITION_REGISTERS[r]:
                        if c in self.current_moves[c]:
                            self.current_moves[c].remove(c)

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
        return dst in equivalent_conditions

    def is_impossible(self, src: ConditionType, delay: int, dst: ConditionType, delta: int) -> bool:
        impossible_conditions = self.__explore_conditions(
                {ConditionType(src.value ^ 1)}, delay, delta)
        equivalent_conditions = self.__explore_conditions({src}, delay, delta)
        return (dst in impossible_conditions
                or ConditionType(dst.value ^ 1) in equivalent_conditions)

    # def __join(self, a: ConditionType, b: ConditionType):
    #     if self.lookup[a] == self.lookup[b]: return
    #     equivalence_class = set()
    #     for c in self.lookup[a]:
    #         equivalence_class.add(c)
    #     for c in self.lookup[b]:
    #         equivalence_class.add(c)
    #     for c in equivalence_class:
    #         self.lookup[c] = equivalence_class
    #     for c in equivalence_class:
    #         if ConditionType(c.value ^ 1) in equivalence_class:
    #             equivalence_class.add(ConditionType.UNCONDITIONAL)

### Condition util ###

def is_cond_move(i: Instruction) -> bool:
    match i.opcode:
        case 'mv' | 'or' | 'and':
            if (_get_register(i.operands[-2]) in CONDITION_REGISTERS
                and _get_register(i.operands[-1]) in CONDITION_REGISTERS):
                return True
    return False

def is_cond_set(i: Instruction) -> bool:
    match i.opcode:
        case 'mvk':
            if (unwrap(_get_register(i.operands[-1])) in CONDITION_REGISTERS):
                return True
    return False

# please forgive the following lines
def _get_register(o: Operand) -> Register:
    match o:
        case RegisterOperand(r) | RegisterPairOperand(r, _):
            return r
    raise ValueError

def _get_value(o: Operand) -> int:
    match o:
        case ImmediateOperand(v):
            return v
    raise ValueError


