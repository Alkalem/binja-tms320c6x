import binaryninja as _bn

from .arch import TMS320C67x


TMS320C67x.register()
_arch = _bn.architecture.Architecture['TMS320C67x']
_bn.binaryview.BinaryViewType['ELF'].register_arch(
    0x9c60, _bn.enums.Endianness.LittleEndian, _arch
)
