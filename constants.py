
ARCH_SIZE = 4
LOAD_BASE = 0x400

REGISTER_NAMES = [
    f'A{i}' for i in range(16)
] + [
    f'B{i}' for i in range(16)
]