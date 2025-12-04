import binaryninja as _bn

from .arch import TMS320C67x, C67Call, TMS320C6x


TMS320C67x.register()
_arch = _bn.architecture.Architecture['TMS320C67x']
_cc = C67Call(arch=_arch, name='C67call')
_arch.register_calling_convention(_cc)
_arch.default_calling_convention = _cc
_bn.binaryview.BinaryViewType['ELF'].register_arch(
    0x9c60, _bn.enums.Endianness.LittleEndian, _arch
)

TMS320C6x.register()
_arch = _bn.architecture.Architecture['TMS320C6x']
_bn.binaryview.BinaryViewType['ELF'].register_arch(
    140, _bn.enums.Endianness.LittleEndian, _arch
)
