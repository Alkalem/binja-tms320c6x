# Planned features and open Tasks

Disassembly:
- Test cases for info, disassembly and lifting inputs
- Showing full disassembly for header-based FPs
- Disassembling SPKERNEL(R) correctly without SPLOOP(R) in same block

Analysis:
- Function creation (registering call targets, skipping non-function blocks)
- Error handling (detection, tagging locations)
- Nested SPLOOP epilog block
- Fully supporting delayed conditional branches and calls
- Delay checks of split blocks and checks for short loops (prolog)

Lifting:
- Top-level lifting architecture for parallel instructions and branch delay
- Templates for unary and binary opcodes, and for operands
- Function-based lifting when supported
    - SPLOOP lifting
    - pending branch lifting
- Calling conventions
- Registering complex opcodes not supported by IL as intrinsics

Assembly:
- Simple assembly bit flipping (completely changing to arbitrary instructions will require working assembler)
- Inverting of conditional branches (i.e. the condition)
- Always branch (conditional branch -> unconditional)
- NOPing of instructions
