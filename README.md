# Binary Ninja TMS320C6x Plugin

This plugin adds support for the TMS320C6000 DSP architecture family to binja. Support currently extends up to ISA C674.
Disassembly is complete, for other features see the detailed limitations below. Block analysis is implemented for delayed branches and non-reloading SPLOOPs, lifting currently translates a small set of instructions.

## Installation

Simply clone this project into your binja plugin folder. Alternatively, download and extract it to this location.
This plugin requires the C6x disassembler included as submodule to work.

## Features / Limitations

Look up the docs of this plugin for a detailed list of features and plans.

- Disassembly, including parallel instructions, is complete. Disassembly of header-based fetch packets is based on analysis context.
- Block analysis uses a custom recursive descent algorithm. Call detection and return detection are based on heuristics. Shifting condition registers and reloading SPLOOPs are known to produce incorrect control flow right now.
- Lifting supports a small subset of instructions. Instruction delay is handled with temporary registers. Function-based lifting serializes parallel and delayed execution. Conditional execution is supported. Currently, branches are the only delayed instructions that are delayed across basic blocks. Conflicts that other delayed results could cause are ignored.
- Multiple architecture features entirely lack lifting. This is the case for SPLOOPs, control registers, 40-bit and long calculations to name a few.
- A default calling convention is provided both for the official compiler and the TMS320C67x tiny C variant.
- This plugin supports features up to the TMS320C674x architecture, but lifting only supports a small subset of instructions. Extended support is in development.

If a feature you expected is missing or not working as you expected, feel free to open an issue. Please include a minimal sample with bug reports to aid reproduction.

## Contributions

This plugin is currently in active development, but contributions are welcome!
Bug reports, fixes and similar changes help a lot. Please refrain from purely AI-driven contributions (keep a human in the loop).
Please coordinate larger changes beforehand to avoid conflicts or duplicate work.
