# Analysis

## Interface

Inputs:
- arch: instance of `TMS320C6x`-type architecture
- func: general function information, contains BinaryView, immutable
- context: settings input and output object, non-local state
- view: global binary information, reading raw bytes

Outputs (via context):
- basic blocks: created and added using context, contain instruction bytes and outgoing edges
- function_arch_context: headers at block boundaries; SPLOOP ii, pending branches and their equivalence groups

The architecture is assumed to be fixed for each binary. Its use during analysis is mostly to annotate this fact in the result. Apart from this, it provides disassembly, instruction info and maximum instruction size.

As the type of the function arch context can be chosen freely, it is set as a container that carries all required information for generating disassembly tokens and lifting. During basic block analysis, the algorithm operates at function-level and has access to global information (through the BinaryView). As such, it can store results for the more limited interfaces of tokenizing and lifting.

## Branch Types

Instruction info provides a basic categorization of branch types for an instruction. This includes a branch type from an enum. However, this enum `BranchType` mixes different categorizations - by condition, by target or special types. For example, an indirect branch can also be conditional.

The condition type is therefore used with two different meanings: branch type and edge type. 
The branch type is used for functional categories like direct, indirect or return. The condition type of the instruction carries condition information.
An edge type is also a branch type, but may differ from the branch type. Binary Ninja uses the branch type provided for outgoing edges not only for further analysis. True and False branches result in green and red coloring of the outgoing edges. Because Calls and returns are not shown as edges, they should not show as conditional edge even if they are in fact conditional.

Analysis of fitting edge types is performed during unification of a branch slot. Here, all branches triggering in the same cycle are known. This is used to assign True or False case to conditional branches, and to create fallthrough FalseBranch edges.

Types used in instruction info (can all be conditional):
- Unconditional: direct branches (actually conditional as well)
- Indirect: register relative branches
- FunctionReturn: branch using return register or exception return
- Exception: software exceptions or detected CPU exceptions
- UserDefined: SPLOOP implicit branches

Additionally as edge types:
- True: conditional branch (usually branch taken)
- False: negated condition branch or fallthrough (branch not taken)

## Condition Tracking

Branches are generally delayed by 5 cycles and can be conditional. For this reason, multiple additional instructions can be executed before a branch is taken. Especially, this can be used to queue additional branches.

A common compiler pattern uses subsequent branches with identical conditions. Queued branches are carried over to the target block of the first branch and trigger there. If multiple branches use the same condition, then the queued branches should be treated as unconditional if the first branch is taken.

More complex variants of this pattern may not use only a single condition value or register. It is possible - and also used by the compiler - to move conditions between condition registers. That typically uses move instructions and conditionally setting condition registers to true or false.

To eliminate impossible branches and detect conditional branches unconditionally following others, this plugin tracks condition registers. Writes of any kind, but especially moves and conditionally setting these registers are taken into account. Queued branches are evaluated based on the active branch condition. 

Tracking of condition values is limited, however. It is built for common compiler patterns and ignores other registers. This plugin does not aim to implement full value set analysis, not even for conditions. Condition analysis may not be complete, but it simplifies graphs for common cases without simplifying too much for complex cases.
