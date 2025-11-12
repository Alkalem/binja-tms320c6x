# Binary Ninja TMS320C67x Plugin

This plugin adds support for the TMS320C67x DSP architecture to binja. 
Disassembly should be completed, other features are WIP.

## Installation

Simply clone this project into your binja plugin folder.
Alternatively, download and extract it to this location.

## Features / Limitations

- Disassembly, including parallel instructions, is complete.
- Block analysis still uses the default algorithm. That limits correctness for edge cases of this architecture.
- Lifting supports a small subset of instructions. Instruction delay is handled with temporary registers and batch lifting for delayed instructions. This is currently unreliable and incorrect for most parallel instructions. Conditions are not lifted yet.
- This plugin only supports the TMS320C67x architecture, not its extension TMS320C67x+ or related architectures from the family.

## Planned updates

- Custom block analysis algorithm for delayed branches.
- Lifting of conditions.
- More complete lifting of the instruction set.
- Reliable batch lifting of delayed and or parallel instructions. This likely requires function-level lifting or a similarly reliable way to control lifting packets.
- Reordering of instructions to minimize the use of temporary registers and simplify later analysis steps.

## Contributions

This plugin is currently in active development, but contributions are welcome!
Bug reports, fixes and similar changes help a lot.
Please coordinate larger changes beforehand to avoid conflicts or duplicate work.

## License

[MIT](https://github.com/Alkalem/binja-tms320c67x/blob/main/LICENSE)
