from llvmlite import ir
import llvmlite.binding as llvm
import sys
import os

if len(sys.argv) != 3:
	print("usage: python3 compiler.py <input> <output.ll>", file=sys.stderr)
	sys.exit(1)

input_path = sys.argv[1]
output_path = sys.argv[2]

if os.path.exists(output_path):
	os.remove(output_path)

def compile_error(line_number,message):
	print(f"compilation error: line {line_number}: {message}",file=sys.stderr)
	sys.exit(1)

with open(input_path,"r") as f:
	lines=f.readlines()

program_lines = []

for line_number, line in enumerate(lines,start=1):
	line=line.strip()
	if not line:
		continue
	program_lines.append((line_number,line))

I32 = ir.IntType(32)
I8 = ir.IntType(8)

module = ir.Module(name="practice1")
module.triple = llvm.get_default_triple()

main = ir.Function(
    module,
    ir.FunctionType(I32, []),
    name="main"
)

entry = main.append_basic_block("entry")
builder = ir.IRBuilder(entry)

printf = ir.Function(
    module,
    ir.FunctionType(
        I32,
        [ir.PointerType(I8)],
        var_arg=True
    ),
    name="printf"
)

text = b"Program exit with result %d\n\0"

fmt = ir.GlobalVariable(
    module,
    ir.ArrayType(I8, len(text)),
    name="fmt"
)

fmt.linkage = "private"
fmt.global_constant = True

fmt.initializer = ir.Constant(
    ir.ArrayType(I8, len(text)),
    bytearray(text)
)

symbols = {}

def valid_name(name):
	return name and (name[0].isalpha() or name[0]=="_") and all(c.isalnum() or c=="_" for c in name) and name not in ["int","exit"]

def get_value(token,line_number):
	token=token.strip()

	if token.isdigit():
		return ir.Constant(I32,int(token))

	if token in symbols:
		return builder.load(symbols[token])

	compile_error(line_number,f"undeclared variable '{token}'")

exit_found=False

for index,(line_number, line) in enumerate(program_lines):
	if line.startswith("int "):
		name = line[4:].strip()

		if not valid_name(name):
			compile_error(line_number,f"invalid variable name '{name}'")

		if name in symbols:
			compile_error(line_number,f"variable '{name}' already declared")

		symbols[name] = builder.alloca(I32, name=name)

	elif ":=" in line:
		left, right = line.split(":=", 1)

		left = left.strip()
		right = right.strip()

		if not valid_name(left):
			compile_error(line_number,f"invalid variable name '{left}'")

		if left not in symbols:
			compile_error(line_number,f"undeclared variable '{left}'")

		if right.isdigit():
			builder.store(
				ir.Constant(I32, int(right)),
				symbols[left]
			)

		elif right in symbols:
			value = builder.load(symbols[right])
			builder.store(value, symbols[left])

		else:
			found=False

			for op in ["+", "-", "*"]:
				if op in right:
					a, b = right.split(op,1)

					if not a.strip() or not b.strip():
						compile_error(line_number,"cannot parse assignment")

					left_value=get_value(a,line_number)
					right_value=get_value(b,line_number)

					if op =="+":
						result=builder.add(left_value,right_value)
					elif op == "-":
						result=builder.sub(left_value,right_value)
					elif op == "*":
						result=builder.mul(left_value,right_value)

					builder.store(result,symbols[left])
					found=True
					break

			if not found:
				compile_error(line_number,"cannot parse assignment")

	elif line.startswith("exit "):
		name = line[5:].strip()

		if name not in symbols:
			compile_error(line_number,f"undeclared variable '{name}'")

		if index != len(program_lines)-1:
			compile_error(line_number,"exit must be the last statement")

		value = builder.load(symbols[name])

		fmt_ptr = builder.bitcast(fmt,ir.PointerType(I8))

		builder.call(printf,[fmt_ptr, value])

		builder.ret(ir.Constant(I32,0))

		exit_found=True

	else:
		compile_error(line_number,"cannot parse line")

if not exit_found:
	compile_error(len(lines),"no exit statement")

with open(output_path,"w") as f:
	f.write(str(module))
