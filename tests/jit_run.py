





import ctypes
from pathlib import Path
import sys

import llvmlite.binding as llvm


def main():
    if len(sys.argv) != 2:
        print('usage: python tests/jit_run.py output.ll', file=sys.stderr)
        return 2
    llvm.initialize_native_target()
    llvm.initialize_native_asmprinter()
    libc = ctypes.CDLL(None)
    for name in ('printf', 'scanf'):
        llvm.add_symbol(name, ctypes.cast(getattr(libc, name), ctypes.c_void_p).value)
    target = llvm.Target.from_default_triple()
    tm = target.create_target_machine()
    module = llvm.parse_assembly(Path(sys.argv[1]).read_text(encoding='utf-8'))
    module.verify()
    with llvm.create_mcjit_compiler(module, tm) as engine:
        engine.finalize_object()
        result = ctypes.CFUNCTYPE(ctypes.c_int)(engine.get_function_address('main'))()
        libc.fflush(None)
        return result


if __name__ == '__main__':
    sys.exit(main())
