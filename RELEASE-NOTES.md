# KARN v1.0.0 — The Agent's Language

## First Release

A token-minimal, platform-agnostic programming language designed specifically for AI agents.

---

## Why KARN?

AI agents are writing code in languages designed for humans. Python, TypeScript, Rust—all optimized for readability. But agents don't need readability—they need token efficiency, deterministic execution, and multi-platform output from a single source.

**KARN delivers:**

- **76% fewer tokens than Python** for equivalent logic
- **3 codegen targets** — C (native binary), JavaScript (Node.js), Web (HTML), Python
- **Error as value** — no exceptions, every I/O returns Ok|Err
- **Full stdlib** — http, fs, log, env, json, math, time, str, crypto, db
- **Ecosystem interop** — from pip, from npm, from cargo, from sys

---

## Quick Demo

```bash
# Install
pip install karn-lang

# Run
echo '! "Hello from KARN"' > hello.kn
karn run hello.kn
# → Hello from KARN!

# Compile to C → native binary
karn build hello.kn --target c
gcc -o hello hello.c -lm
./hello
# → Hello from KARN!

# Same source, JavaScript
karn build hello.kn --target js
node hello.js
# → Hello from KARN!
```

---

## What's Built

- **Interpreter**: Full tree-walk evaluator with REPL
- **JIT Mode**: Profiles hot functions, compiles to native on-the-fly
- **C Codegen**: Outputs C source → gcc/clang → native binary
- **JavaScript Codegen**: Outputs Node.js-compatible JS
- **Web Codegen**: Outputs self-contained HTML
- **Python Codegen**: Outputs portable Python 3
- **91 tests** passing across lexer, parser, interpreter, codegen

---

## For Agents

KARN is designed to be consumed by agents, not just used by them:

- **Agent docs**: [AGENT.md](https://github.com/karn-lang/karn/blob/main/AGENT.md)
- **Machine-readable spec**: [karn-spec.json](https://raw.githubusercontent.com/karn-lang/karn/main/karn-spec.json)

---

## Install

```bash
pip install karn-lang
npm install karn-lang
```

---

## Links

- **GitHub**: https://github.com/karn-lang/karn
- **PyPI**: https://pypi.org/project/karn-lang/
- **npm**: https://www.npmjs.com/package/karn-lang
- **Website**: https://karn-lang.dev (or GitHub Pages)

---

## License

MIT — See [LICENSE](https://github.com/karn-lang/karn/blob/main/LICENSE)

---

## Credits

Built by **Eulogik** — the team behind **Evolucent AI** (building AI agent teams and workflows).
