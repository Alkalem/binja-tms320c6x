# Scenarios

This document collects problematic patterns and sequences of instructions a compiler could create. Problematic in the sense that they brake decompilation approaches like single-instruction disassembly and lifting. 

<!-- Each scenario: title, pattern, occurrence, problem(s), solution(s) -->


## Parallel execution

### Single cycle interleaving
* Pattern: group of instructions in an EP write to registers another of them reads, for example `mv a, b; || mv b, a`
* Occurrence: regular, TI compiler does this often by default
* Problem: Single-instruction lifting cannot be correct.
* Solution: Instructions of an EP need to be lifted together. If the pattern occurs, reads need to be lifted before writes. At least one value needs to be preserved over a register write, for example with a temp register.

### Multi-block EP
* Pattern: jump in the middle of EP splits it in multiple basic blocks
* Occurrence: unusual, mentioned in reference guide as example
* Problem: Related problems for EPs, delay etc. may span multiple blocks. Analysis and lifting solutions limited to a single basic block cannot cover this.
* Solution: Analysis and lifting need to be function-based with information passed between basic blocks. Interleaving in this scenario can be modeled as conditional execution during lifting. That requires that all source blocks set value for the synthetic condition register.


## Conditionals

### Conditionally unconditional
* Pattern: Conditional instructions following an initial check against the same condition register or a register equivalent by data flow. This applies especially but not exclusively to multiple branches queued under the same condition. For example `[A0] b <label1>; [A0] b <label2>`.
* Occurrence: frequent, used by TI compiler by default
* Problem: Treating each instruction separate, decompilation results may be too general. Conditional use as described may constrain following instructions. For example, (delayed) instructions after a conditional branch should treat that condition as true.
* Solution: limited constant propagation for conditionals during analysis and lifting, should overrule unconstrained conditionals
