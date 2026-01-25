# Binary Ninja TMS320C6x Plugin

This plugin adds support for the TMS320C6000 DSP architecture family to binja. Support currently extends up to ISA C674.
Disassembly should be completed, other features are WIP as discussed below.

## Installation

Simply clone this project into your binja plugin folder. Alternatively, download and extract it to this location.
This plugin requires the C6x disassembler included as submodule to work.

## Features / Limitations

Look up the docs of this plugin for a detailed list of features and plans.

- Disassembly, including parallel instructions, is complete. Disassembly of header-based fetch packets requires support from binary ninja.
- Block analysis uses a custom recursive descent algorithm. Call detection and return detection are limited.
- Lifting supports a small subset of instructions and the Tiny C Compiler only. Instruction delay is handled with temporary registers and batch lifting for delayed instructions. This is currently unreliable and incorrect for most parallel instructions. Conditions are not lifted yet.
- This plugin only supports the TMS320C67x architecture yet, not its extension TMS320C67x+ or related architectures from the family. Extended support is in development.

If a feature you expected is missing or not working as you expected, feel free to open an issue. Please include a minimal sample with bug reports to aid reproduction.

## Contributions

This plugin is currently in active development, but contributions are welcome!
Bug reports, fixes and similar changes help a lot.
Please coordinate larger changes beforehand to avoid conflicts or duplicate work.
