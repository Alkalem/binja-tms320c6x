
ARCH_SIZE = 4
DW_SIZE = 2*ARCH_SIZE
HW_SIZE = ARCH_SIZE//2
LOAD_BASE = 0x400

REGISTER_NAMES = [
    f'A{i}' for i in range(16)
] + [
    f'B{i}' for i in range(16)
]

INSTRUCTION_DELAY = {
    'b': 5,
    'ldb': 4,
    'ldw': 4,
    'mpyi': 8,
    'stb': 4,
    'stw': 4,
}
