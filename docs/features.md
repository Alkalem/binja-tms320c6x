# Plugin Features

General:
- Two architecture variants, one for the C6000 ELF machine value and one for the Tiny C compiler.

Disassembly:
- Disassembly of all instructions up to ISA version C674.
- Parallel instructions are displayed as prefix of the following instruction.
- Conditions are displayed as prefix to instructions.

Analysis:
- Custom block analysis algorithm for delayed branches.
- Pending branches are are registered for target blocks. They are expected to be equal for multiple call sources and empty for calls.
- Two branches in the same execute packet have a shared false branch or none at all depending on their conditions.
- Simple SPLOOPs are analyzed correctly, reloading is not supported yet.

Lifting:
- Only supported for a subset of TMS320C67x.

