# Lifting

This is less a documentation how lifting works than a collection of notes and ideas for lifting this architecture.

Tasks:
- Lifting algorithm that fulfills the following criteria:
    - [ ] Lifting of register reads/writes in correct order.
    - [ ] Lifting of loads/stores in correct order.
    - [ ] Handling operants independent of opcode.
    - [ ] Simple way to implement additional opcodes.
    - [ ] Support for varying input and output sizes.
- [ ] Lifting of conditions

There are multiple goals to optimize a lifting algorithm for:
- Minimize number of used temporary registers.
- Simplify expressions to equivalent more concise IL.
- Transform IL to facilitate higher level analysis.

Ideas for optimizations to achieve these goals:
- Track register usages of instructions.
    - Only allocate temp registers on demand if a conflict occurs.
    - Reorder instructions to reduce conflicts.
- Joining conditional expressions together by condition.
- Lifting pseudo-instructions to simpler IL forms.
- Replacing complex IL with dedicated IL instructions where possible. For example, using push/pop or set_reg_split if conflicts can be resolved.

Challenges:
- Memory operands access registers both reading and optionally writing. Their access is documented for memory only, leaving out the register accesses. Register write back is a second output not from the operation but from address calculation of the operand.
- Instructions set contains composite instructions for convenience. While they shorten disassembly, lifting might comprise multiple operations. Instructions might translate to multiple IL trees.
