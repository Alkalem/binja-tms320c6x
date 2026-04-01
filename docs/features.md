# Plugin Features

General:
- Two architecture variants, one for the C6000 ELF machine value and one for the Tiny C compiler.

Disassembly:
- Disassembly of all instructions up to ISA version C674.
- Parallel instructions are displayed as prefix of the following instruction.
- Conditions are displayed as prefix to instructions.

Analysis:
- Custom block analysis algorithm for delayed branches.
- Pending branches are registered for target blocks. They are expected to be equal for multiple call sources and empty for calls.
- Two branches in the same execute packet have a shared false branch or none at all depending on their conditions.
- Simple SPLOOPs are analyzed correctly, reloading is not supported yet.
- Call detection uses pending instructions and target address as heuristic. If no return address write can be detected, branches are treated as tailcall.

Lifting:
- Only supported for a small subset of TMS320C674x.
- General structure serializes parallel and delayed instructions to IL.
- Conditionals are lifted for each instruction.

Patching:
- Inverting of conditional branches (i.e. the condition)
- Always branch (conditional branch -> unconditional)
- NOPing of instructions
