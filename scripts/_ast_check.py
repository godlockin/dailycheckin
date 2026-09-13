"""AST parse check"""
import ast
import sys

path = sys.argv[1]
try:
    ast.parse(open(path).read())
    print(f"{path}: OK")
except SyntaxError as e:
    print(f"{path}: {e}")
    print(f"  line {e.lineno}: {e.text}")
    print(f"  offset: {e.offset}")
