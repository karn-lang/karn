# KARN — The Agent's Language

A token-minimal, platform-agnostic programming language built for AI agents.

**4x denser than Python. 3 execution modes. 3 codegen targets. Every ecosystem.**

```karn
@web  #http #db.pg #auth

type User:{id:N, name:S, role:S}

^getUser->req:
  tok  = auth.verify(req.header.token)?
  user = db.q("users", {id:req.p.id})?
  !user

http.serve(3000, {"/users/:id": getUser})
```

## Why KARN for AI Agents

- **Token economy** — 76% fewer tokens than Python for equivalent logic. More code fits in your output limit.
- **Context window efficiency** — Smaller source = more of the program fits in context. You reason about the whole thing.
- **Deterministic semantics** — No exceptions, no hidden control flow. Every I/O returns `Ok|Err`. You always know what executes.
- **Multi-platform, one source** — Generate once. Compile to C (native), JS (Node.js), HTML (browser), or Python.
- **Full ecosystem access** — `from pip numpy`, `from npm react`, `from cargo serde`. One line.

## Quick Start

### Install

```bash
git clone https://github.com/karn-lang/karn.git
cd karn
pip install -e .
```

### Hello World

```bash
echo '! "Hello from KARN"' > hello.kn
karn run hello.kn
```

### Run Examples

```bash
karn run examples/hello.kn
karn run examples/fibonacci.kn        # → 55
karn run examples/collections.kn      # → 42
```

### REPL

```bash
karn repl
```

### Type Check

```bash
karn check examples/*.kn
```

### Compile

```bash
karn build hello.kn --target c        # → hello.c
karn build hello.kn --target js       # → hello.js
karn build hello.kn --target web      # → hello.html
karn build hello.kn --target python   # → hello.python.py
karn build hello.kn --target macos-arm64  # → hello (native binary)
karn build hello.kn --target linux-x64    # → hello (ELF binary)
```

## Language Reference

### Variables

```karn
x = 42              -- immutable bind
~count = 0           -- mutable bind
name:S = "karn"      -- typed bind
const PI:N = 3.14    -- constant
```

### Functions

```karn
add->a:N b:N:N       -- function with types
  a + b

^export->x:           -- exported (public)
  !x * 2

square = x -> x * x  -- lambda
```

### Types

```karn
type User:{id:N, name:S, email:S?}
type Tree<T>:{val:T, kids:[Tree<T>]}
type Result<T>:{Ok:T | Err:S}
```

### Error Handling

```karn
data = http.get(url)?           -- propagate error up
val  = cache.get(key)??fallback -- fallback on error
```

### Concurrency

```karn
[a, b, c] = taskA() & taskB() & taskC()  -- parallel
auth.verify(tok) |> db.q("users")         -- sequential pipe
result = primary()|~fallback()             -- race
data = http.get(url).retry(3).t(5000)?    -- retry + timeout
```

### Collections

```karn
doubled = items*(x -> x * 2)   -- map
actives = users%(u -> u.active) -- filter
sequence = 1..10               -- range
```

### Pattern Matching

```karn
match result{
  Ok(v)  -> !v
  Err(e) -> log.err(e) |> !nil
}
```

### Interop

```karn
from pip numpy as np
from npm react as R
from cargo serde as serde
from sys ffmpeg as ff
```

## Execution Modes

| Mode | Command | Use Case |
|------|---------|----------|
| Interpreted | `karn run script.kn` | Instant iteration, REPL |
| JIT | `karn run --jit server.kn` | Warm services, ML loops |
| Compiled | `karn build app.kn --target c` | Production deployment |

## Codegen Targets

| Target | Output | How |
|--------|--------|-----|
| C | `.c` source | `gcc -o output input.c -lm` |
| JavaScript | `.js` | `node output.js` |
| Web | `.html` | Open in browser |
| Python | `.python.py` | `python output.python.py` |
| macOS ARM | native binary | Auto-compiled with gcc |
| Linux x64 | native binary | Auto-compiled with gcc |
| WASM | `.wasm` | `emcc` or `clang --target=wasm32` |

## Comparison

| | KARN | Python | Rust | TypeScript |
|---|---|---|---|---|
| Token density | ~2.1/LOC | ~6.8/LOC | ~11.5/LOC | ~9.2/LOC |
| Platform targets | All | Server | Native+WASM | Web+Node |
| Error handling | Result+chain | Exceptions | Result | Mixed |
| Async model | Default, 1 op | async/await | Tokio | async/await |
| Ecosystem | pip+npm+cargo+sys | pip native | cargo+C FFI | npm native |

## Project Structure

```
karn-lang/
├── files/karn.py          # Runtime: lexer, parser, interpreter, codegen, REPL
├── index.html             # Landing page
├── docs.html              # AI Agent Documentation
├── karn-spec.json         # Machine-readable language spec
├── examples/              # .kn example programs
├── tests/                 # Test suite (91 tests)
├── pyproject.toml         # Package config
└── README.md
```

## For AI Agents

- **Agent Docs**: [docs.html](docs.html) — Complete spec written for agents, not humans
- **Machine-readable spec**: [karn-spec.json](karn-spec.json) — Parseable JSON with full language definition
- **Token savings**: 76% vs Python, 83% vs TypeScript, 89% vs Rust

## License

MIT
