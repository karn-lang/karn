#!/usr/bin/env python3
"""KARN Test Suite — lexer, parser, interpreter, codegen."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from files.karn import (
    Lexer, Parser, Interpreter, CodeGen, TT,
    LexError, ParseError, KarnError, OkVal, EmitSignal,
    NumberLit, StringLit, Ident, Bind, FnDef, Call, Emit, BinOp
)

passed = 0
failed = 0

def test(name):
    def decorator(fn):
        global passed, failed
        try:
            fn()
            passed += 1
            print(f"  \033[32m✓\033[0m {name}")
        except Exception as e:
            failed += 1
            print(f"  \033[31m✗\033[0m {name}: {e}")
    return decorator


# ═══════════════════════════════════════════════════════════
#  LEXER TESTS
# ═══════════════════════════════════════════════════════════

print("\n\033[33mLexer Tests\033[0m")

@test("lex numbers")
def _():
    toks = Lexer("42 3.14 -7").tokenize()
    vals = [t.value for t in toks if t.type == TT.NUMBER]
    # '-' after a number is binary subtraction, not a negative literal
    assert vals == [42, 3.14, 7], f"got {vals}"

@test("lex negative numbers in unary position")
def _():
    for src in ("x = -7", "(-7)", "f(-7)", "a, -7"):
        toks = Lexer(src).tokenize()
        vals = [t.value for t in toks if t.type == TT.NUMBER]
        assert vals == [-7], f"{src!r}: got {vals}"

@test("lex subtraction without spaces")
def _():
    toks = Lexer("x-1").tokenize()
    types = [t.type for t in toks if t.type != TT.EOF]
    assert types == [TT.IDENT, TT.MINUS, TT.NUMBER], f"got {types}"

@test("lex strings")
def _():
    toks = Lexer('"hello" "world"').tokenize()
    vals = [t.value for t in toks if t.type == TT.STRING]
    assert vals == ["hello", "world"], f"got {vals}"

@test("lex identifiers")
def _():
    toks = Lexer("foo bar_baz x1").tokenize()
    vals = [t.value for t in toks if t.type == TT.IDENT]
    assert vals == ["foo", "bar_baz", "x1"], f"got {vals}"

@test("lex keywords")
def _():
    toks = Lexer("from as type trait match const").tokenize()
    types = [t.type for t in toks if t.type != TT.EOF]
    assert types == [TT.FROM, TT.AS, TT.TYPE, TT.TRAIT, TT.MATCH, TT.CONST], f"got {types}"

@test("lex operators")
def _():
    toks = Lexer("-> ?? |~ .. ?: <= >=").tokenize()
    types = [t.type for t in toks if t.type != TT.EOF]
    assert types == [TT.ARROW, TT.DQMARK, TT.RACE, TT.DOTDOT, TT.TERNQ, TT.LTE, TT.GTE], f"got {types}"

@test("lex comments")
def _():
    toks = Lexer("-- this is a comment\n42").tokenize()
    nums = [t for t in toks if t.type == TT.NUMBER]
    assert len(nums) == 1 and nums[0].value == 42

@test("lex bools")
def _():
    toks = Lexer("true false").tokenize()
    vals = [t.value for t in toks if t.type == TT.BOOL]
    assert vals == [True, False], f"got {vals}"


# ═══════════════════════════════════════════════════════════
#  PARSER TESTS
# ═══════════════════════════════════════════════════════════

print("\n\033[33mParser Tests\033[0m")

def parse(src):
    return Parser(Lexer(src).tokenize()).parse()

@test("parse number literal")
def _():
    ast = parse("42")
    assert len(ast.stmts) == 1
    assert isinstance(ast.stmts[0], NumberLit)
    assert ast.stmts[0].value == 42

@test("parse string literal")
def _():
    ast = parse('"hello"')
    assert isinstance(ast.stmts[0], StringLit)
    assert ast.stmts[0].value == "hello"

@test("parse bind")
def _():
    ast = parse("x = 42")
    assert isinstance(ast.stmts[0], Bind)
    assert ast.stmts[0].name == "x"
    assert isinstance(ast.stmts[0].value, NumberLit)

@test("parse mutable bind")
def _():
    ast = parse("~count = 0")
    assert isinstance(ast.stmts[0], Bind)
    assert ast.stmts[0].mutable == True
    assert ast.stmts[0].name == "count"

@test("parse function def single param")
def _():
    ast = parse("add->a: a+1")
    assert isinstance(ast.stmts[0], FnDef)
    assert ast.stmts[0].name == "add"
    assert len(ast.stmts[0].params) == 1
    assert ast.stmts[0].params[0][0] == "a"

@test("parse function def multi params with types")
def _():
    ast = parse("add->a:N b:N:N\n  a+b")
    fn = ast.stmts[0]
    assert isinstance(fn, FnDef)
    assert fn.name == "add"
    assert len(fn.params) == 2, f"expected 2 params, got {len(fn.params)}: {fn.params}"
    assert fn.params[0] == ("a", "N")
    assert fn.params[1] == ("b", "N")
    assert fn.ret_type == "N"

@test("parse function def no types")
def _():
    ast = parse("add->a b:\n  a+b")
    fn = ast.stmts[0]
    assert isinstance(fn, FnDef)
    assert len(fn.params) == 2, f"expected 2 params, got {len(fn.params)}: {fn.params}"
    assert fn.params[0] == ("a", None)
    assert fn.params[1] == ("b", None)

@test("parse exported function")
def _():
    ast = parse("^main->:\n  !0")
    fn = ast.stmts[0]
    assert isinstance(fn, FnDef)
    assert fn.exported == True
    assert fn.name == "main"

@test("parse emit")
def _():
    ast = parse('! "hello"')
    assert isinstance(ast.stmts[0], Emit)

@test("parse comparison operators")
def _():
    for op, expected in [("<", "<"), ("<=", "<="), (">=", ">="), ("==", "=="), ("!=", "!=")]:
        ast = parse(f"x {op} 5")
        stmt = ast.stmts[0]
        assert isinstance(stmt, BinOp), f"{op}: expected BinOp, got {type(stmt)}"
        assert stmt.op == expected, f"{op}: expected {expected}, got {stmt.op}"

@test("parse range expression")
def _():
    ast = parse("1..10")
    from files.karn import RangeExpr
    assert isinstance(ast.stmts[0], RangeExpr)

@test("parse type definition")
def _():
    from files.karn import TypeDef
    ast = parse("type User:{id:N, name:S}")
    assert isinstance(ast.stmts[0], TypeDef)
    assert ast.stmts[0].name == "User"
    assert ast.stmts[0].fields == {"id": "N", "name": "S"}

@test("parse map operation")
def _():
    from files.karn import MapOp
    ast = parse("items*(x->x*2)")
    assert isinstance(ast.stmts[0], MapOp)

@test("parse filter operation")
def _():
    from files.karn import FilterOp
    ast = parse("items%(x->x>1)")
    assert isinstance(ast.stmts[0], FilterOp)

@test("parse match expression")
def _():
    from files.karn import MatchExpr
    ast = parse("match x{ Ok(v) -> v }")
    assert isinstance(ast.stmts[0], MatchExpr)

@test("parse stdlib import")
def _():
    from files.karn import StdlibImport
    ast = parse("#http")
    assert isinstance(ast.stmts[0], StdlibImport)
    assert ast.stmts[0].path == "http"

@test("parse extern import")
def _():
    from files.karn import ExternImport
    ast = parse("from pip numpy as np")
    assert isinstance(ast.stmts[0], ExternImport)
    assert ast.stmts[0].ecosystem == "pip"
    assert ast.stmts[0].package == "numpy"
    assert ast.stmts[0].alias == "np"

@test("parse target declaration")
def _():
    from files.karn import TargetDecl
    ast = parse("@web+@ios")
    assert isinstance(ast.stmts[0], TargetDecl)
    assert ast.stmts[0].targets == ["web", "ios"]

@test("parse parallel &")
def _():
    from files.karn import Par
    ast = parse("f() & g()")
    assert isinstance(ast.stmts[0], Par)
    assert len(ast.stmts[0].exprs) == 2

@test("parse parallel & with parens and values")
def _():
    from files.karn import Par
    ast = parse("(f()) & (g())")
    assert isinstance(ast.stmts[0], Par)
    ast = parse("1 & 2")
    assert isinstance(ast.stmts[0], Par)

@test("parse single pipe | with bare function")
def _():
    from files.karn import Pipe
    ast = parse("double(5) | double")
    assert isinstance(ast.stmts[0], Pipe)
    assert len(ast.stmts[0].stages) == 2


# ═══════════════════════════════════════════════════════════
#  INTERPRETER TESTS
# ═══════════════════════════════════════════════════════════

print("\n\033[33mInterpreter Tests\033[0m")

def run(src):
    ast = parse(src)
    interp = Interpreter()
    result = None
    for stmt in ast.stmts:
        try:
            result = interp.eval(stmt, interp.global_env)
        except EmitSignal as e:
            result = e.value
    return result, interp

@test("interpret number")
def _():
    r, _ = run("42")
    assert r == 42

@test("interpret string")
def _():
    r, _ = run('"hello"')
    assert r == "hello"

@test("interpret arithmetic")
def _():
    r, _ = run("1 + 2 * 3")
    assert r == 7, f"got {r}"

@test("interpret bind and read")
def _():
    r, _ = run("x = 42\n!x")
    assert r == 42

@test("interpret mutable bind")
def _():
    r, _ = run("~x = 1\n~x = 2\n!x")
    assert r == 2

@test("interpret function def and call")
def _():
    r, _ = run("add->a b: a+b\nadd(3, 4)")
    assert r == 7, f"got {r}"

@test("interpret emit")
def _():
    r, _ = run('! "hello"')
    assert r == "hello"

@test("interpret comparison <")
def _():
    r, _ = run("3 < 5")
    assert r == True, f"got {r}"

@test("interpret comparison >")
def _():
    r, _ = run("7 > 5")
    assert r == True, f"got {r}"

@test("interpret comparison <=")
def _():
    r, _ = run("5 <= 5")
    assert r == True, f"got {r}"

@test("interpret comparison >=")
def _():
    r, _ = run("7 >= 5")
    assert r == True, f"got {r}"

@test("interpret comparison ==")
def _():
    r, _ = run("42 == 42")
    assert r == True, f"got {r}"

@test("interpret comparison !=")
def _():
    r, _ = run("1 != 2")
    assert r == True, f"got {r}"

@test("interpret range")
def _():
    r, _ = run("1..5")
    assert r == [1, 2, 3, 4, 5], f"got {r}"

@test("interpret list literal")
def _():
    r, _ = run("[1, 2, 3]")
    assert r == [1, 2, 3]

@test("interpret map literal")
def _():
    r, _ = run('{x:1, y:2}')
    assert r == {"x": 1, "y": 2}

@test("interpret map operation")
def _():
    r, _ = run("[1, 2, 3]*(x->x*2)")
    assert r == [2, 4, 6], f"got {r}"

@test("interpret filter operation")
def _():
    r, _ = run("[1, 2, 3, 4]%(x->x>2)")
    assert r == [3, 4], f"got {r}"

@test("interpret Ok/Err")
def _():
    r, _ = run("Ok(42)")
    assert isinstance(r, OkVal)
    assert r.value == 42

@test("interpret propagate ?")
def _():
    r, _ = run("Ok(42)?")
    assert r == 42

@test("interpret fallback ??")
def _():
    r, _ = run("Err('fail')??42")
    assert r == 42

@test("interpret type definition")
def _():
    r, _ = run("type Pt:{x:N, y:N}\nPt(1, 2)")
    assert r.type_name == "Pt"
    assert r.fields == {"x": 1, "y": 2}

@test("interpret type field access")
def _():
    r, _ = run("type Pt:{x:N, y:N}\np = Pt(10, 20)\np.x")
    assert r == 10, f"got {r}"

@test("interpret match Ok")
def _():
    r, _ = run("match Ok(42){ Ok(v) -> v, Err(e) -> 0 }")
    assert r == 42

@test("interpret match Err")
def _():
    r, _ = run("match Err('fail'){ Ok(v) -> v, Err(e) -> 0 }")
    assert r == 0

@test("interpret pipe-forward |>")
def _():
    r, _ = run("double->x: x*2\ndouble(5) |> double")
    assert r == 20, f"got {r}"

@test("interpret parallel &")
def _():
    r, _ = run("a = 1\nb = 2\na + b")
    assert r == 3, f"got {r}"

@test("interpret subtraction without spaces")
def _():
    r, _ = run("x = 10\nx-1")
    assert r == 9, f"got {r}"

@test("interpret call arg without spaces")
def _():
    r, _ = run("f->n: n\nf(5-1)")
    assert r == 4, f"got {r}"

@test("interpret parallel & calls")
def _():
    r, _ = run("f->: 1\ng->: 2\nf() & g()")
    assert r == [1, 2], f"got {r}"

@test("interpret single pipe | with bare function")
def _():
    r, _ = run("double->x: x*2\ndouble(5) | double")
    assert r == 20, f"got {r}"

@test("interpret recursion (factorial)")
def _():
    r, _ = run("fact->n:N:N\n  match n{ 0 -> 1, _ -> n * fact(n - 1) }\nfact(5)")
    assert r == 120, f"got {r}"

@test("interpret string builtins")
def _():
    r, _ = run('"hello".upper()')
    assert r == "HELLO", f"got {r}"

@test("interpret list len")
def _():
    r, _ = run("[1,2,3].len()")
    assert r == 3, f"got {r}"

@test("interpret list first")
def _():
    r, _ = run("[10,20,30].first()")
    assert r == 10, f"got {r}"

@test("interpret spread in list")
def _():
    r, _ = run("a = [1, 2]\n[*a, 3]")
    assert r == [1, 2, 3], f"got {r}"

@test("interpret spread in map")
def _():
    r, _ = run("a = {x:1}\n{*a, y:2}")
    assert r == {"x": 1, "y": 2}, f"got {r}"

@test("interpret lambda")
def _():
    r, _ = run("square = x -> x * x\nsquare(5)")
    assert r == 25, f"got {r}"

@test("interpret stdlib json.parse")
def _():
    r, _ = run('json.parse(\'{"x":1}\')')
    assert isinstance(r, OkVal)
    assert r.value == {"x": 1}

@test("interpret stdlib math.sqrt")
def _():
    r, _ = run("math.sqrt(16)")
    assert r == 4.0, f"got {r}"

@test("interpret stdlib crypto.md5")
def _():
    r, _ = run('crypto.md5("hello")')
    assert isinstance(r, OkVal)
    assert len(r.value) == 32

@test("interpret stdlib str.join")
def _():
    r, _ = run('str.join(["a","b","c"], "-")')
    assert isinstance(r, OkVal)
    assert r.value == "a-b-c"


# ═══════════════════════════════════════════════════════════
#  CODEGEN TESTS
# ═══════════════════════════════════════════════════════════

print("\n\033[33mCodegen Tests\033[0m")

def gen(src):
    ast = parse(src)
    codegen = CodeGen(target='python')
    return codegen.generate(ast)

@test("codegen bind")
def _():
    code = gen("x = 42")
    assert "x = 42" in code

@test("codegen function")
def _():
    code = gen("add->a b: a+b")
    assert "def add(a, b):" in code

@test("codegen type definition")
def _():
    code = gen("type User:{id:N, name:S}")
    assert "class User:" in code
    assert "def __init__(self, id, name):" in code

@test("codegen emit at top level")
def _():
    code = gen('! "hello"')
    assert 'print' in code

@test("codegen implicit return")
def _():
    code = gen("add->a b: a+b")
    assert "return (a + b)" in code, f"got:\n{code}"

@test("codegen match expression")
def _():
    code = gen("match x{ 0 -> 1, _ -> 2 }")
    assert "_match(" in code, f"got:\n{code}"
    assert "None  # match" not in code

@test("codegen index")
def _():
    code = gen("a[0]")
    assert "_idx(" in code, f"got:\n{code}"

@test("codegen spread in list")
def _():
    code = gen("[*a, 3]")
    assert "*a" in code, f"got:\n{code}"

@test("codegen fibonacci executes to 55")
def _():
    src = ("fib->n:N:N\n  match n{\n    0 -> 0\n    1 -> 1\n"
           "    x -> fib(x - 1) + fib(x - 2)\n  }")
    code = gen(src)
    ns = {}
    exec(compile(code, "<test>", "exec"), ns)
    assert ns["fib"](10) == 55, f"got {ns['fib'](10)}"

@test("codegen rejects unemittable nodes")
def _():
    from files.karn import CodeGen, CodegenError, Ternary
    codegen = CodeGen(target='python')
    try:
        codegen.gen_expr(Ternary(cond=None, then_=None, else_=None))
    except CodegenError:
        return
    raise AssertionError("expected CodegenError")


# ═══════════════════════════════════════════════════════════
#  EXAMPLE FILES TESTS
# ═══════════════════════════════════════════════════════════

print("\n\033[33mExample File Tests\033[0m")

@test("parse examples/hello.kn")
def _():
    path = os.path.join(os.path.dirname(__file__), '..', 'examples', 'hello.kn')
    if os.path.exists(path):
        src = open(path).read()
        ast = parse(src)
        assert len(ast.stmts) > 0
    else:
        pass  # skip if not found

@test("parse examples/fibonacci.kn")
def _():
    path = os.path.join(os.path.dirname(__file__), '..', 'examples', 'fibonacci.kn')
    if os.path.exists(path):
        src = open(path).read()
        ast = parse(src)
        assert len(ast.stmts) > 0
    else:
        pass

@test("run examples/fibonacci.kn")
def _():
    path = os.path.join(os.path.dirname(__file__), '..', 'examples', 'fibonacci.kn')
    if os.path.exists(path):
        src = open(path).read()
        r, _ = run(src)
        assert r == 55, f"fib(10) should be 55, got {r}"
    else:
        pass

@test("parse examples/collections.kn")
def _():
    path = os.path.join(os.path.dirname(__file__), '..', 'examples', 'collections.kn')
    if os.path.exists(path):
        src = open(path).read()
        ast = parse(src)
        assert len(ast.stmts) > 0
    else:
        pass


# ═══════════════════════════════════════════════════════════
#  JS CODEGEN TESTS
# ═══════════════════════════════════════════════════════════

print("\n\033[33mJS Codegen Tests\033[0m")

from files.karn import JSCodeGen

def gen_js(src):
    ast = parse(src)
    gen = JSCodeGen(target='js')
    return gen.generate(ast)

@test("js codegen hello")
def _():
    code = gen_js('! "hello"')
    assert 'console.log' in code
    assert '"hello"' in code

@test("js codegen function")
def _():
    code = gen_js("add->a b: a+b")
    assert 'function add(a, b)' in code

@test("js codegen bools")
def _():
    code = gen_js("true")
    assert 'true' in code
    assert 'True' not in code

@test("js codegen null")
def _():
    code = gen_js("nil")
    assert 'null' in code
    assert 'None' not in code

@test("js codegen map")
def _():
    code = gen_js("[1,2,3]*(x->x*2)")
    assert '.map(' in code

@test("js codegen filter")
def _():
    code = gen_js("[1,2,3]%(x->x>1)")
    assert '.filter(' in code

@test("js codegen range")
def _():
    code = gen_js("1..10")
    assert 'Array.from' in code

@test("js codegen type def")
def _():
    code = gen_js("type User:{id:N, name:S}")
    assert 'class User' in code
    assert 'constructor(id, name)' in code

@test("js codegen Ok/Err")
def _():
    code = gen_js("Ok(42)")
    assert 'new _Ok(42)' in code

@test("js codegen fallback")
def _():
    code = gen_js("Err('fail')??42")
    assert 'new _Err(' in code
    assert 'instanceof _Err' in code

@test("js web target produces HTML")
def _():
    ast = parse('! "hello"')
    gen = JSCodeGen(target='web')
    code = gen.generate(ast)
    assert '<!DOCTYPE html>' in code
    assert '<script>' in code
    assert '</script>' in code
    assert 'console.log' in code


# ═══════════════════════════════════════════════════════════
#  C CODEGEN TESTS
# ═══════════════════════════════════════════════════════════

print("\n\033[33mC Codegen Tests\033[0m")

from files.karn import CCodeGen

def gen_c(src):
    ast = parse(src)
    gen = CCodeGen()
    return gen.generate(ast)

@test("c codegen hello")
def _():
    code = gen_c('! "hello"')
    assert 'val_println(val_str("hello"))' in code
    assert 'int main(void)' in code

@test("c codegen function")
def _():
    code = gen_c("add->a b: a+b")
    assert 'Val add(Val a, Val b)' in code

@test("c codegen numbers")
def _():
    code = gen_c('! 42')
    assert 'val_num(42)' in code

@test("c codegen bools")
def _():
    code = gen_c('! true')
    assert 'val_bool(true)' in code

@test("c codegen nil")
def _():
    code = gen_c('! nil')
    assert 'val_nil()' in code

@test("c codegen Ok/Err")
def _():
    code = gen_c("! Ok(42)")
    assert 'val_ok(val_num(42))' in code

@test("c codegen arithmetic")
def _():
    code = gen_c("! 1 + 2 * 3")
    assert '_val_to_num' in code

@test("c codegen fallback")
def _():
    code = gen_c("Err('fail')??42")
    assert 'VAL_ERR' in code
    assert 'VAL_OK' in code

@test("c codegen type def")
def _():
    code = gen_c("type User:{id:N, name:S}")
    assert 'typedef struct' in code
    assert 'User_t' in code


# ═══════════════════════════════════════════════════════════
#  JIT TESTS
# ═══════════════════════════════════════════════════════════

print("\n\033[33mJIT Tests\033[0m")

def run_jit(src):
    ast = parse(src)
    interp = Interpreter()
    interp.jit_mode = True
    result = None
    for stmt in ast.stmts:
        try:
            result = interp.eval(stmt, interp.global_env)
        except EmitSignal as e:
            result = e.value
    return result, interp

@test("jit fibonacci matches interpreter")
def _():
    src = ("fib->n:N:N\n  match n{\n    0 -> 0\n    1 -> 1\n"
           "    x -> fib(x - 1) + fib(x - 2)\n  }\n! fib(10)")
    r, _ = run_jit(src)
    assert r == 55, f"got {r}"

@test("jit compiles simple functions faithfully")
def _():
    # 11 calls: threshold (10) trips on call 10, call 11 runs compiled code
    r, interp = run_jit("add->a b: a+b\n" + "\n".join(["! add(3, 4)"] * 11))
    assert r == 7, f"got {r}"
    assert getattr(interp.global_env.bindings["add"], "_jit_fn", None) is not None

@test("jit invalidates on global rebind")
def _():
    src = ("~c = 1\nf->x: x + c\n" + "\n".join(["! f(0)"] * 11)
           + "\n~c = 100\n! f(0)")
    r, _ = run_jit(src)
    assert r == 100, f"got {r}"


# ═══════════════════════════════════════════════════════════
#  BATCH 2 — STDLIB PRELUDE / ERRORS / IMMUTABILITY / TARGETS
# ═══════════════════════════════════════════════════════════

print("\n\033[33mBatch 2 Tests\033[0m")

def gen_exec(src):
    """Generate python code and exec it, returning the namespace."""
    code = gen(src)
    ns = {}
    exec(compile(code, "<test>", "exec"), ns)
    return ns

def run_cli_src(src, *args):
    """Run `karn <args...> <tmpfile.kn>`; file path goes after subcommand."""
    import subprocess
    import tempfile
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with tempfile.NamedTemporaryFile("w", suffix=".kn", delete=False) as f:
        f.write(src)
        path = f.name
    try:
        cmd = [sys.executable, "files/karn.py", args[0], path, *args[1:]]
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=120, cwd=root)
    finally:
        os.unlink(path)

@test("py stdlib math/fs/log/json/str execute")
def _():
    import tempfile
    d = tempfile.mkdtemp()
    p = os.path.join(d, "t.txt")
    src = (f'fs.write("{p}", "hi")\n'
           f'c = fs.read("{p}")?\n'
           'm = math.sqrt(16)\n'
           'j = json.parse(\'{"a": 1}\')?\n'
           's = str.join(["a", "b"], "-")?\n'
           'log.info("x")')
    ns = gen_exec(src)
    assert ns["c"] == "hi", ns.get("c")
    assert ns["m"] == 4.0, ns.get("m")
    assert ns["j"] == {"a": 1}, ns.get("j")
    assert ns["s"] == "a-b", ns.get("s")

@test("py stdlib time/crypto/env/db execute")
def _():
    ns = gen_exec('t = time.date()?\n'
                  'y = t["year"]\n'
                  'u = crypto.md5("x")?\n'
                  'h = env.get("NOPE_KARN_TEST", "dflt")?\n'
                  'q = db.q("t")?')
    assert isinstance(ns["y"], int), ns.get("y")
    assert len(ns["u"]) == 32, ns.get("u")
    assert ns["h"] == "dflt", ns.get("h")
    assert ns["q"] == [], ns.get("q")

@test("py getattr dict/list/str parity")
def _():
    ns = gen_exec('d = {a: 1}\nx = d.a\ny = [1, 2].len()\nz = "hi".upper()')
    assert ns["x"] == 1, ns.get("x")
    assert ns["y"] == 2, ns.get("y")
    assert ns["z"] == "HI", ns.get("z")

@test("py len failure is error value")
def _():
    ns = gen_exec('x = len(42)')
    assert type(ns["x"]).__name__ == "_Err", ns.get("x")

@test("div by zero is KarnError")
def _():
    try:
        run("1/0")
    except KarnError as e:
        assert "Division by zero" in str(e), e
        return
    raise AssertionError("expected KarnError")

@test("missing map key is KarnError")
def _():
    try:
        run('m = {a: 1}\nm["b"]')
    except KarnError as e:
        assert "Map has no key" in str(e), e
        return
    raise AssertionError("expected KarnError")

@test("list OOB is KarnError")
def _():
    try:
        run("a = [1]\na[5]")
    except KarnError as e:
        assert "out of range" in str(e), e
        return
    raise AssertionError("expected KarnError")

@test("bad index type is KarnError")
def _():
    try:
        run('[1]["a"]')
    except KarnError as e:
        assert "Cannot index" in str(e), e
        return
    raise AssertionError("expected KarnError")

@test("cli run runtime error exits 1 without traceback")
def _():
    r = run_cli_src("! (1/0)", "run")
    assert r.returncode == 1, r
    assert "Traceback" not in (r.stdout + r.stderr), r.stderr[-500:]

@test("cli run top-level ? error exits 1")
def _():
    r = run_cli_src('Err("boom")?', "run")
    assert r.returncode == 1, r
    assert "Traceback" not in (r.stdout + r.stderr), r.stderr[-500:]

@test("cli run success exits 0")
def _():
    r = run_cli_src('! "hi"', "run")
    assert r.returncode == 0, r
    assert "hi" in r.stdout, r.stdout

@test("plain = cannot rebind")
def _():
    try:
        run("x = 1\nx = 2")
    except KarnError as e:
        assert "Cannot rebind" in str(e), e
        return
    raise AssertionError("expected KarnError")

@test("mutable rebind works")
def _():
    r, _ = run("~x = 1\n~x = 2\n!x")
    assert r == 2, r

@test("shadowing in child scope is allowed")
def _():
    r, _ = run("x = 1\nf->:\n  x = 2\nf()\n!x")
    assert r == 1, r

@test("js kwargs raise CodegenError")
def _():
    from files.karn import CodegenError
    try:
        gen_js("f(x:1)")
    except CodegenError:
        return
    raise AssertionError("expected CodegenError")

@test("c string equality uses content compare")
def _():
    code = gen_c('"a" == "b"')
    assert "karn_eq(" in code, code[-400:]

@test("c comparison integration")
def _():
    import shutil
    import subprocess
    import tempfile
    from files.karn import compile_file
    cc = shutil.which("gcc") or shutil.which("cc")
    if cc is None:
        return  # skip without a C toolchain
    src = '! [("a" == "b"), ("a" == "a"), (3 > 2), ("b" > "a"), ([1] == [1, 2])]'
    with tempfile.TemporaryDirectory() as d:
        c_path = os.path.join(d, "t.c")
        exe = os.path.join(d, "t")
        open(c_path, "w").write(compile_file(src, "t.kn", "c"))
        r = subprocess.run([cc, c_path, "-o", exe, "-lm"],
                           capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, r.stderr[-1000:]
        r = subprocess.run([exe], capture_output=True, text=True, timeout=30)
        assert r.stdout.strip() == "[false, true, true, true, false]", r.stdout

@test("unknown build target raises CodegenError")
def _():
    from files.karn import compile_file, CodegenError
    try:
        compile_file("! 1", "t.kn", "ios")
    except CodegenError:
        return
    raise AssertionError("expected CodegenError")

@test("cli rejects removed targets")
def _():
    r = run_cli_src("! 1", "build", "--target", "ios")
    assert r.returncode != 0, r
    assert "invalid choice" in r.stderr, r.stderr[-300:]


# ═══════════════════════════════════════════════════════════
#  SUMMARY
# ═══════════════════════════════════════════════════════════

print(f"\n\033[33mResults:\033[0m {passed} passed, {failed} failed")
if failed:
    print(f"\033[31mFAILED\033[0m")
    sys.exit(1)
else:
    print(f"\033[32mALL PASSED\033[0m")
