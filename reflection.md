# Reflection — Languages and Compilers Design

### 1. Most useful features

I found compiler optimizations and memory management particularly useful. I liked learning how a compiler can remove unnecessary code without changing the program's behavior.For example, in our compiler, `RemoveUnusedVariables` analyzes which variables and functions are actually used. In tests, the function unused(`)` is never called, while twice(`)` is needed. With `--remove-unused`, unnecessary functions can be removed.
Garbage collection was also one of useful topics. I was already working with it in C# during some other courses, so it was fascinating to see how it works on low level. It was explained how memory occupied by unreachable objects can be automatically cleaned during execution.

### 2. Features not covered properly

I don't think there were any important topics that were not fully covered. The course was well structured, and the practical assignments showed how compiler components work together. During this practice we combined earlier implemented features like structures, functions, semantic checks, and IR generation to compile a complete program into an executable. I found this progression helpful because we could see how the functionality developed step-by-step during the course.

### 3. Three things that surprised me the most

1. garbage collection algorithms. I have had really superficial view about how programming languages determine which objects are no longer needed and manage this on the low level. Comparing reference counting with tracing garbage collection, which identifies reachable objects, helped me understand why different approaches have different advantages and limitations.
2. The second was function inlining. Initially, it wasn't obvious to me that a compiler could replace a function call with the function's body. It was new concept for me and I didn\`t know in which case it can be used.
3. The third was LLVM IR and portability. Before the course, I didn't understand in which way compiler may support different processor architectures. It was interesting to learn that  back end of compiler makes an intermediate representation that different backends can translate into machine code.

### 4. Concepts that are still unclear

I still find calls, the ABI, and linking somewhat difficult to understand, especially how they interact at a lower level. I am still not completely confident about how external symbols are resolved or how calling conventions determine the passing of arguments and return values. 
I also find the differences in passing structures between C and our language complicated.&#x20;

### 5. Other ideas and suggestions

I would suggest including more detailed demonstrations of the complete compilation process and separated processes also. Maybe it could be useful if we add some interactive parts to our practise lessons where we would work in groups and could create a schema with detailed path of code and data managing at different stages of our compiler. For example we could build a full process of GC  working while using some examples.
It could also be helpful to have some demonstrations where we could compare code before and after optimization to see exactly what the compiler removes and why.
