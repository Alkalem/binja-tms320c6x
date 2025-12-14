
ARCH_SIZE = 4
DW_SIZE = 2*ARCH_SIZE
HW_SIZE = ARCH_SIZE//2
FP_SIZE = 8*ARCH_SIZE
LOAD_BASE = 0x400

BRANCH_DELAY = 5
INSTRUCTION_DELAY = {
    'b': 5,
    'ldb': 4,
    'ldw': 4,
    'mpyi': 8,
    'stb': 4,
    'stw': 4,
}
