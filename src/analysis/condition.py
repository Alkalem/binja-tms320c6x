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

from tms320c6x_disassembler.types import ConditionType, Instruction, RW, RegisterOperand, RegisterPairOperand, Register, ImmediateOperand

from ..util import op_get_high_register, op_get_register, op_get_value, unwrap


CONDITION_REGISTERS = {
    Register.A0: (ConditionType.A0, ConditionType.NOT_A0),
    Register.A1: (ConditionType.A1, ConditionType.NOT_A1),
    Register.A2: (ConditionType.A2, ConditionType.NOT_A2),
    Register.B0: (ConditionType.B0, ConditionType.NOT_B0),
    Register.B1: (ConditionType.B1, ConditionType.NOT_B1),
    Register.B2: (ConditionType.B2, ConditionType.NOT_B2),
}

class ConditionState:
    def __init__(self) -> None:
        self.moves: list[dict[ConditionType, set[ConditionType]]] = list()
        self.queued_discards: list[list[tuple[ConditionType, ConditionType]]] = list()
        self.lookup = {r: {r,} for r in CONDITION_REGISTERS}
        self.equivalences = [self.lookup.copy() for _ in range(6)]
        self.__init_moves()

    def __split(self, a: Register):
        if not (a in CONDITION_REGISTERS):
            raise ValueError('Invalid non-condition register')
        if len(self.lookup[a]) == 1: return # already separate
        equivalence_class = self.lookup[a].difference({a,})
        for r in equivalence_class:
            self.lookup[r] = equivalence_class
        self.lookup[a] = {a,}

    def __join(self, a: Register, b: Register):
        if not (a in CONDITION_REGISTERS and b in CONDITION_REGISTERS):
            raise ValueError('Invalid non-condition register')
        if b in self.lookup[a]: return # already joined
        self.__split(b)
        equivalence_class = self.lookup[a].union(self.lookup[b])
        for r in equivalence_class:
            self.lookup[r] = equivalence_class

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

    def __discard(self, delay: int, reg: Register):
        if delay > 0:
            for c in CONDITION_REGISTERS[reg]:
                self.__queue_discard(delay, c, c)
        else:
            for c in CONDITION_REGISTERS[reg]:
                self.current_moves[c].discard(c)

    def __discard_cond(self, delay: int, src: ConditionType, dst: Register):
        if delay > 0:
            for c in CONDITION_REGISTERS[dst]:
                self.__queue_discard(delay, src, c)
        else:
            for c in CONDITION_REGISTERS[dst]:
                self.current_moves[src].discard(c)

    def __add_move(self, src: ConditionType, dst: ConditionType):
        if ConditionType(dst.value ^ 1) in self.current_moves[src]:
            self.current_moves[src].remove(ConditionType(dst.value ^ 1))
        self.current_moves[src].add(dst)

    def __add_moves(self, src: Register, dst: Register):
        if not (src in CONDITION_REGISTERS and dst in CONDITION_REGISTERS):
            raise ValueError('Move with non-condition register')
        for a, b in zip(CONDITION_REGISTERS[src], CONDITION_REGISTERS[dst]):
            self.__add_move(a, b)
        self.__discard(0, dst)

    def process(self, i: Instruction):
        if i.condition == ConditionType.UNCONDITIONAL and is_cond_move(i):
            match i.operands[-2]:
                case RegisterOperand(src) | RegisterPairOperand(src, _):
                    dst = op_get_register(i.operands[-1])
                    self.__add_moves(src, dst)
            if isinstance(i.operands[-2], RegisterPairOperand):
                src = op_get_high_register(i.operands[-2])
                dst = op_get_high_register(i.operands[-1])
                if (src in CONDITION_REGISTERS and dst in CONDITION_REGISTERS):
                    self.__add_moves(src, dst)
        elif is_cond_set(i):
            if i.condition == ConditionType.UNCONDITIONAL:
                pass # TODO: target is always true or always false
            else:
                src = i.condition
                dst_index = 0 if op_get_value(i.operands[0]) else 1
                dst = CONDITION_REGISTERS[op_get_register(i.operands[-1])][dst_index]
                self.__add_move(src, dst)
        else:
            for operand in i.operands:
                # Check if condition register is written to
                written_registers = list()
                if operand.access_info.rw in (RW.read_write, RW.write):
                    match operand:
                        case RegisterOperand(r) | RegisterPairOperand(r, _):
                            if r not in CONDITION_REGISTERS:
                                continue
                            delay = operand.access_info.low_first
                            written_registers.append((delay, r))
                        case _:
                            continue
                    match operand:
                        case RegisterPairOperand(_, h): 
                            if h in CONDITION_REGISTERS:
                                delay = operand.access_info.high_first
                                written_registers.append((delay, h))
                else: continue

                if i.condition == ConditionType.UNCONDITIONAL:
                    for delay, r in written_registers:
                        self.__discard(delay - 1, r)
                else:
                    for delay, r in written_registers:
                        self.__discard_cond(delay - 1, i.condition, r)

    def end_ep(self):
        self.moves.append(self.current_moves)
        for r, cs in CONDITION_REGISTERS.items():
            if any([c not in self.current_moves[c] for c in cs]):
                self.__split(r)
        for r, cs in CONDITION_REGISTERS.items():
            if any([c not in self.current_moves[c] for c in cs]): continue
            for c in self.current_moves[cs[0]]:
                if c.value & 1 != cs[0].value & 1: continue
                if ConditionType(c.value ^ 1) not in self.current_moves[cs[1]]: continue
                self.__join(r, unwrap(c.register))
        self.equivalences.append(self.lookup.copy())
        self.__init_moves()

    def __explore_conditions(self, start: set[ConditionType], delay: int, delta: int) -> set[ConditionType]:
        equivalent_conditions = set()
        for c in start:
            if c.register is None:
                equivalent_conditions.add(c)
            else:
                registers = self.equivalences[-delay-2][unwrap(c.register)]
                i = CONDITION_REGISTERS[unwrap(c.register)].index(c)
                equivalent_conditions.update({CONDITION_REGISTERS[r][i] 
                        for r in registers})
        if delay == 0: return equivalent_conditions
        # -1 because moves in parallel to check belong to the same chain
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
        equivalent_conditions = self.__explore_conditions({src}, delay, delta)
        return ConditionType(dst.value ^ 1) in equivalent_conditions

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


