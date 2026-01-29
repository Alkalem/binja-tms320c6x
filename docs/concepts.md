# Plugin Concepts

This document starts with an explanation of basic concepts. Skip it or use it as reference if you are familiar with Binja architecture plugins.

## Basics

This is a binary ninja architecture plugin. Any architecture plugin needs to subclass and register an `Architecture` from the API. There are three main tasks in order to support decompilation of an architecture:
1. Inform Binja about instruction size and outgoing branches (`get_instruction_info`).
2. Provide disassembly tokens (`get_instruction_text`).
3. Lift instructions to Binja's low-level intermediate language (LLIL) (`get_instruction_low_leve_il`).

For a working implementation, some ground work is additionally required. Our architecture needs to define basic architecture properties as class members. This includes information like instruction alignment, address size and register definitions. However, information about individual instructions during analysis is provided by the methods mentioned above.

There are additional methods an architecture can implement. These are usually to support optional features for the architecture like modifying assembly. This plugin also implements some additional classes to provide information about calling conventions.

The above explanations hide an important detail: Binja generally assumes that the main tasks can be performed instruction by instruction. It provides an address and instruction bytes to the architecture methods. The architecture cannot easily maintain analysis state, as it may be used to concurrently analyze multiple binaries. 
In cases where the information on one instruction is not sufficient, there are two intended solutions. 
By increasing the maximum instruction size, the architecture receives bytes for multiple consecutive instructions. This is limited to the end of a basic block for instruction text and lifting. Nevertheless, the additional data can be used for lookaheads or to handle batches of instructions at once.
Lastly, an architecture may implement methods to override analysis at function level. This is currently only supported for basic block analysis.

Basic block analysis is the high level concept to split a binary into functions and basic blocks. It also analyzes the control flow to recover the control flow graph of a function and to detect calls. 
This is usually handled well by a robust default implementation from Binja. It receives branch information from the architecture plugin. For complex cases, branch information may not be sufficient or the results may be incorrect. 
A custom implementation by an architecture plugin completely replaces the basic block analysis for the architecture. One execution should fully analyze a function. This includes assigning instruction bytes to basic blocks, adding control flow edges between blocks, and registering calls as additional analysis targets cross-references. For the analysis, the architecture receives a function handle that can also be used to store additional analysis information for a binary. Custom analyis may decide not to use the instruction info method.

## Overview

This plugin implements multiple architecture variants for different compilers. That is because this project started with a binary compiled with a tiny-C compiler for the C67x. The architecture uses a disassembler library that was written for this project. It can create a disassembler for a specific variant. The plugin uses the most general supported variant if detection is not possible. The variants all share the same `e_machine` value for C6000 ELFs.

We define high halves of the registers for use in lifting (e.g., of `mvkh`).

## Custom Block Analysis

The overall structure of the block analysis is similar to the default algorithm. It iteratively processes basic blocks and outgoing branches until no more changes are made. Branches may add new targets (block or function) for analysis or split a block in two.

The major difference of the custom algorithm is the support for cycle branch delay, pending branches and SPLOOPs. Additionally, as this algorithm was developed specifically for the C6000, irrelevant parts from the default algorithm are omitted and some architecture specific things like return detection are handled during analysis.

Analysis steps through blocks an EP at a time. Branches are added to a queue according to their delay. Conditions are stored together with the branch information. At the end of each EP, the cycle delay of the entire EP is calculated. This amount of cycles is popped from the start of the queue. Branches in the same delay slot are first simplified together to detect exhaustive conditions and to remove multiple false branches. Afterwards, targets of each branch are handled depending on the branch type.

On a branch to a basic block, the algorithm not only registers this block as a target for analysis. Pending branches are taken into account as well. They are stored for each basic block start to correctly analyze the block later. For example, a block might end after one cycle if a branch was queued four cycles earlier. We also compare pending branches if multiple edges converge at one basic block. That is because assumptions for higher level analysis break if one block has different pending branch targets. We want to at least detect such cases.

SPLOOP support is implemented by creating a loop context with the start address when an SPLOOP is detected. This context is carried linearly up to the SPKERNEL(R) instruction. There, the loop start is added as conditional target. A loop context might cross basic block boundaries because conditional branches or incoming branches might split the loop body without ending the SPLOOP in the fallthrough case.

## Lifting

Binary Ninja does not have a concept of parallel execution or delayed instructions. The plugin needs to convert such instructions into an equivalent sequence of IL instructions. This can be done by splitting instructions in parts like reads, calculation and write back. The parts are interleaved as they would be executed on a CPU. By default, binary ninja knows registers and memory addresses and tracks possible value sets for analysis. If a location is written that is required in its current state, simple serialization produces incorrect results. Such data conflicts can be resolved by storing values in temporary registers or sometimes by reordering of instructions.

Delayed instructions can easily produce data conflicts, as parameters or results are accessed multiple cycles later. However, even parallel instructions may produce conflicts. For example, the pair `mv x, y || mv y, x` already requires a temporary register, even though both instructions are single cycle. In consequence, every instruction is treated equally during lifting.

### Pipeline Lifting Algorithm

This approach converts instructions to low-level IL in a way that closely resembles the pipeline execution on a real CPU. Lifting of each cycle follows a simple pattern: 1. lift register reads, 2. lift instructions with complete operands, 3. write results back. Each instruction is split into these parts. Delayed instructions are modeled by scheduling some parts for later cycles. Temporary values are always stored in temporary registers. This pattern should be easier to design and test, however it may use a large number of temp registers.

The delay for register reads and write back of results is documented for each instruction type. For some instructions, there are multiple cycles of delay between the last read cycle and the first write cycle. This degree of freedom for lifting the instruction semantic is resolved by lifting instructions as early as possible. That means, as soon as all operands are available. This reduces the number of temp registers used for each cycle and likely reduces conflicts.

Temp register cycles for MPYDP: earliest lifting: 3+2+1+5+6 = 17, latest lifting: 9+8+7+6 = 30

## Challenges

Binja supports branch delay for its default analysis. However, the value is interpreted as a delay of instructions, not cycles. Consequently, returning the cycle delay of 5 as instruction info will break with parallel instructions and multi-cycle NOPs. As a workaround, the architecture plugin can translate the cycle delay to instruction delay with lookaheads. This requires a high maximum instruction length, but works relatively well. It was the chosen solution before basic block analysis was implemented (see `3fe19b8`).

To correctly analyze branches of the C6000 architectures, the branches of one EP should be analyzed together. For example, two conditional branches with inverted conditions should be detected as exhaustive. A fallthrough edge should be omitted in this case. This can be achieved by aligning branches of multiple instructions in one EP via branch delay or by translating an entire EP to one instruction info.
The above approaches improve the results of the default block analysis. However, they require workarounds and additional analysis while generating instruction info. Also, some control flow patterns cannot be handled by the default algorithm. For example, SPLOOPs and pending branches do not work. 

Disassembly for variants that support header-based FPs requires knowledge of the whole FP up to the last word. Also, SPLOOPs contain an iteration interval that is necessary to disassemble SPKERNEL(R) correctly. In both cases, basic blocks can start after or end before the required words.
As long as Binja does not provide a way to pass additional information or read the binary from `get_instruction_text`, these cases seem impossible to implement correctly.

Compilers for the C6000 often try to use the cycles until delayed instructions complete. This also leads to patterns, where instructions are pending on the end of a basic block. This cannot be lifted correctly with instruction-level lifting as it cannot cross block boundaries. Binja will support function-level lifting to solve this issue.
