"""
backend/execution/ — run the snippet and turn it into facts.

models.py — the value objects (Loop, Step, PythonStep) the rest of the tree reads
tracer.py — exec under sys.settrace, snapshot locals/stdout per line
loops.py  — post-trace `for`-loop analysis (header lag fix, current index)

Deliberately import-free: every endpoint imports the submodules directly, so
nothing here executes during a serverless cold start.
"""
