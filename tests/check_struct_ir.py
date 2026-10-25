import subprocess
import sys
import tempfile
from pathlib import Path

import llvmlite.binding as llvm

ROOT = Path(__file__).resolve().parents[1]


def generate(name):
    with tempfile.TemporaryDirectory() as d:
        output = Path(d) / "output.ll"
        source = ROOT / "tests" / "ok" / (name + ".txt")
        result = subprocess.run(
            [sys.executable, str(ROOT / "compiler.py"), str(source), str(output)],
            capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
        text = output.read_text()
        llvm.parse_assembly(text).verify()
        return text


def main():
    circle = generate("struct_circle")
    assert '%"Point" = type {i32, i32}' in circle
    assert '%"Circle" = type {%"Point", i32}' in circle
    assert '%"p" = alloca %"Point"' in circle
    assert '%"c" = alloca %"Circle"' in circle
    assert circle.count('getelementptr inbounds') >= 4
    assert 'store i32 10' in circle
    assert 'store i32 20' in circle
    assert 'store i32 5' in circle
    assert 'load %"Point"' in circle
    assert 'store %"Point"' in circle

    copied = generate("struct_copy")
    assert 'load %"Point"' in copied
    assert 'store %"Point"' in copied

    widened = generate("struct_widen_field")
    assert 'sext i32' in widened
    assert 'store i64' in widened

    print("PASS: named structs, alloca, indexed GEP, stores, copies, widening")


if __name__ == "__main__":
    main()
