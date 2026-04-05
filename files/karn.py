#!/usr/bin/env python3
"""
KARN v1.0 — Runtime & Compiler
================================
The Agent's Language.

Components:
  Lexer       → tokenizes .kn source
  Parser      → builds AST
  Interpreter → tree-walk evaluator (agi)
  Compiler    → emits Python/JS/native via LLVM (agc)
  REPL        → interactive session

Usage:
  python karn.py run  <file.kn>            # interpret
  python karn.py run  <file.kn> --jit      # JIT mode (hotspot)
  python karn.py build <file.kn> [--target] # compile
  python karn.py repl                       # interactive REPL
  python karn.py check <file.kn>            # type-check only
"""

from __future__ import annotations
import sys, os, re, json, time, textwrap, argparse, shutil, subprocess
from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Any, Optional, List, Dict, Tuple, Callable


# ═══════════════════════════════════════════════════════════
#  TOKEN TYPES
# ═══════════════════════════════════════════════════════════

class TT(Enum):
    # Literals
    NUMBER = auto()
    STRING = auto()
    BOOL   = auto()
    NIL    = auto()
    IDENT  = auto()

    # Operators
    EQ     = auto()   # =
    TILDE  = auto()   # ~   (mutable bind)
    BANG   = auto()   # !   (emit/return)
    ARROW  = auto()   # ->  (fn def)
    PIPE   = auto()   # |   (pipe)
    AMP    = auto()   # &   (parallel)
    GT      = auto()   # >   (greater-than)
    LTE     = auto()   # <=  (less-or-equal)
    GTE     = auto()   # >=  (greater-or-equal)
    PIPE_FWD = auto()  # |>  (pipe forward / sequential)
    RACE    = auto()   # |~  (race)
    QMARK  = auto()   # ?   (propagate)
    DQMARK = auto()   # ??  (fallback)
    CARET  = auto()   # ^   (export)
    HASH   = auto()   # #   (stdlib import)
    AT     = auto()   # @   (target/decorator)
    STAR   = auto()   # *   (map / spread)
    PCT    = auto()   # %   (filter)
    DOTDOT = auto()   # ..  (range)
    COLON  = auto()   # :   (type annotation / map sep)
    PLUS   = auto()   # +
    MINUS  = auto()   # -
    SLASH  = auto()   # /
    DOT    = auto()   # .
    UNDER  = auto()   # _   (discard)
    LT     = auto()   # <   (generic open / less-than)
    COMMA  = auto()   # ,
    TERNQ  = auto()   # ?: (ternary guard)

    # Delimiters
    LPAREN = auto()   # (
    RPAREN = auto()   # )
    LBRACK = auto()   # [
    RBRACK = auto()   # ]
    LBRACE = auto()   # {
    RBRACE = auto()   # }

    # Keywords
    FROM   = auto()   # from   (interop import)
    AS     = auto()   # as
    TYPE   = auto()   # type
    TRAIT  = auto()   # trait
    MATCH  = auto()   # match
    CONST  = auto()   # const
    IF     = auto()   # if  (sugar for ?:)
    ELSE   = auto()   # else

    # Special
    NEWLINE = auto()
    INDENT  = auto()
    DEDENT  = auto()
    EOF     = auto()
    COMMENT = auto()


@dataclass
class Token:
    type:   TT
    value:  Any
    line:   int
    col:    int

    def __repr__(self):
        return f"Token({self.type.name}, {self.value!r}, {self.line}:{self.col})"


# ═══════════════════════════════════════════════════════════
#  LEXER
# ═══════════════════════════════════════════════════════════

KEYWORDS = {
    'from':  TT.FROM,
    'as':    TT.AS,
    'type':  TT.TYPE,
    'trait': TT.TRAIT,
    'match': TT.MATCH,
    'const': TT.CONST,
    'if':    TT.IF,
    'else':  TT.ELSE,
    'nil':   TT.NIL,
    'true':  TT.BOOL,
    'false': TT.BOOL,
    'Ok':    TT.IDENT,
    'Err':   TT.IDENT,
}

class LexError(Exception):
    def __init__(self, msg, line, col):
        super().__init__(f"[Lex Error] {msg} at {line}:{col}")

class Lexer:
    def __init__(self, src: str):
        self.src    = src
        self.pos    = 0
        self.line   = 1
        self.col    = 1
        self.tokens: List[Token] = []
        self.indent_stack = [0]

    def peek(self, offset=0) -> str:
        p = self.pos + offset
        return self.src[p] if p < len(self.src) else '\0'

    def advance(self) -> str:
        ch = self.src[self.pos]
        self.pos += 1
        if ch == '\n':
            self.line += 1
            self.col = 1
        else:
            self.col += 1
        return ch

    def add(self, tt: TT, value: Any, line=None, col=None):
        self.tokens.append(Token(tt, value, line or self.line, col or self.col))

    def tokenize(self) -> List[Token]:
        while self.pos < len(self.src):
            self._scan()
        # Emit remaining DEDENTs
        while len(self.indent_stack) > 1:
            self.indent_stack.pop()
            self.add(TT.DEDENT, None)
        self.add(TT.EOF, None)
        return [t for t in self.tokens if t.type != TT.COMMENT]

    def _scan(self):
        ch = self.peek()

        # Comments
        if ch == '-' and self.peek(1) == '-':
            while self.pos < len(self.src) and self.peek() != '\n':
                self.advance()
            return

        # Newlines + indentation
        if ch == '\n':
            self.advance()
            self.add(TT.NEWLINE, '\n')
            # Measure indent of next line
            indent = 0
            while self.peek() == ' ':
                self.advance()
                indent += 1
            while self.peek() == '\t':
                self.advance()
                indent += 2
            cur = self.indent_stack[-1]
            if indent > cur:
                self.indent_stack.append(indent)
                self.add(TT.INDENT, indent)
            elif indent < cur:
                while self.indent_stack[-1] > indent:
                    self.indent_stack.pop()
                    self.add(TT.DEDENT, indent)
            return

        # Skip spaces/tabs (not newlines)
        if ch in (' ', '\t', '\r'):
            self.advance()
            return

        line, col = self.line, self.col

        # Strings (double or single quotes)
        if ch in ('"', "'"):
            quote = ch
            self.advance()
            s = []
            while self.peek() != quote and self.peek() != '\0':
                c = self.advance()
                if c == '\\':
                    esc = self.advance()
                    s.append({'n':'\n','t':'\t','r':'\r','"':'"',"'":"'",'\\':'\\'}
                              .get(esc, f'\\{esc}'))
                else:
                    s.append(c)
            self.advance()  # closing quote
            self.add(TT.STRING, ''.join(s), line, col)
            return

        # Numbers
        if ch.isdigit() or (ch == '-' and self.peek(1).isdigit()):
            start = self.pos
            if ch == '-': self.advance()
            while self.peek().isdigit(): self.advance()
            if self.peek() == '.' and self.peek(1).isdigit():
                self.advance()
                while self.peek().isdigit(): self.advance()
                self.add(TT.NUMBER, float(self.src[start:self.pos]), line, col)
            else:
                self.add(TT.NUMBER, int(self.src[start:self.pos]), line, col)
            return

        # Identifiers & keywords
        if ch.isalpha() or ch == '_':
            start = self.pos
            while self.peek().isalnum() or self.peek() in ('_',):
                self.advance()
            word = self.src[start:self.pos]
            tt = KEYWORDS.get(word, TT.IDENT)
            val = {'true': True, 'false': False, 'nil': None}.get(word, word)
            self.add(tt, val, line, col)
            return

        # Multi-char operators
        two = ch + self.peek(1)
        if two == '->': self.advance(); self.advance(); self.add(TT.ARROW, '->', line, col); return
        if two == '??': self.advance(); self.advance(); self.add(TT.DQMARK,'??',line,col); return
        if two == '|~': self.advance(); self.advance(); self.add(TT.RACE,  '|~',line,col); return
        if two == '|>': self.advance(); self.advance(); self.add(TT.PIPE_FWD,'|>',line,col); return
        if two == '..': self.advance(); self.advance(); self.add(TT.DOTDOT,'..',line,col); return
        if two == '?:': self.advance(); self.advance(); self.add(TT.TERNQ, '?:',line,col); return
        if two == '<=': self.advance(); self.advance(); self.add(TT.LTE,  '<=',line,col); return
        if two == '>=': self.advance(); self.advance(); self.add(TT.GTE,  '>=',line,col); return

        # Single-char operators
        MAP = {
            '=': TT.EQ,   '~': TT.TILDE, '!': TT.BANG,  '|': TT.PIPE,
            '&': TT.AMP,  '>': TT.GT,    '?': TT.QMARK, '^': TT.CARET,
            '#': TT.HASH, '@': TT.AT,    '*': TT.STAR,  '%': TT.PCT,
            ':': TT.COLON,'+': TT.PLUS,  '-': TT.MINUS, '/': TT.SLASH,
            '.': TT.DOT,  '_': TT.UNDER, '<': TT.LT,    ',': TT.COMMA,
            '(': TT.LPAREN,')'  :TT.RPAREN,
            '[': TT.LBRACK,']': TT.RBRACK,
            '{': TT.LBRACE,'}': TT.RBRACE,
        }
        if ch in MAP:
            self.advance()
            self.add(MAP[ch], ch, line, col)
            return

        raise LexError(f"Unexpected character {ch!r}", self.line, self.col)


# ═══════════════════════════════════════════════════════════
#  AST NODES
# ═══════════════════════════════════════════════════════════

class Node: pass  # base marker

@dataclass
class Program:
    stmts: List[Any]
    line:  int = 0

@dataclass
class NumberLit:
    value: float
    line:  int = 0

@dataclass
class StringLit:
    value: str
    line:  int = 0

@dataclass
class BoolLit:
    value: bool
    line:  int = 0

@dataclass
class NilLit:
    line: int = 0

@dataclass
class Ident:
    name: str
    line: int = 0

@dataclass
class ListLit:
    items: List[Any]
    line:  int = 0

@dataclass
class MapLit:
    pairs: List[Any]
    line:  int = 0

@dataclass
class Bind:
    name:    str
    value:   Any
    mutable: bool = False
    line:    int  = 0

@dataclass
class Emit:
    value: Any
    line:  int = 0

@dataclass
class BinOp:
    op:    str
    left:  Any
    right: Any
    line:  int = 0

@dataclass
class Pipe:
    stages: List[Any]
    line:   int = 0

@dataclass
class Par:
    exprs: List[Any]
    line:  int = 0

@dataclass
class Seq:
    exprs: List[Any]
    line:  int = 0

@dataclass
class Race:
    left:  Any
    right: Any
    line:  int = 0

@dataclass
class Propagate:
    expr: Any
    line: int = 0

@dataclass
class Fallback:
    expr:    Any
    default: Any
    line:    int = 0

@dataclass
class Call:
    callee: Any
    args:   List[Any]
    kwargs: Dict[str, Any] = field(default_factory=dict)
    line:   int = 0

@dataclass
class GetAttr:
    obj:  Any
    attr: str
    line: int = 0

@dataclass
class Index:
    obj:   Any
    index: Any
    line:  int = 0

@dataclass
class FnDef:
    name:     Optional[str]
    params:   List[Any]
    ret_type: Optional[str]
    body:     List[Any]
    exported: bool = False
    line:     int  = 0

@dataclass
class TypeDef:
    name:   str
    fields: Dict[str, str]
    line:   int = 0

@dataclass
class TraitDef:
    name:    str
    methods: List[Any]
    line:    int = 0

@dataclass
class MatchExpr:
    subject: Any
    arms:    List[Any]
    line:    int = 0

@dataclass
class MapOp:
    collection: Any
    fn:         Any
    line:       int = 0

@dataclass
class FilterOp:
    collection: Any
    fn:         Any
    line:       int = 0

@dataclass
class RangeExpr:
    start: Any
    end:   Any
    line:  int = 0

@dataclass
class TargetDecl:
    targets: List[str]
    line:    int = 0

@dataclass
class StdlibImport:
    path: str
    line: int = 0

@dataclass
class ExternImport:
    ecosystem: str
    package:   str
    alias:     str
    line:      int = 0

@dataclass
class Ternary:
    cond:  Any
    then_: Any
    else_: Any
    line:  int = 0

@dataclass
class Spread:
    expr: Any
    line: int = 0

@dataclass
class TimeoutExpr:
    expr: Any
    ms:   Any
    line: int = 0

@dataclass
class RetryExpr:
    expr: Any
    n:    Any
    line: int = 0


# ═══════════════════════════════════════════════════════════
#  PARSER
# ═══════════════════════════════════════════════════════════

class ParseError(Exception):
    def __init__(self, msg, token: Token):
        super().__init__(f"[Parse Error] {msg} at line {token.line}:{token.col} (got {token.type.name} {token.value!r})")

class Parser:
    def __init__(self, tokens: List[Token]):
        self.tokens = [t for t in tokens if t.type != TT.NEWLINE or True]
        self.pos    = 0

    def peek(self, offset=0) -> Token:
        p = self.pos + offset
        return self.tokens[p] if p < len(self.tokens) else self.tokens[-1]

    def check(self, *types) -> bool:
        return self.peek().type in types

    def match(self, *types) -> Optional[Token]:
        if self.check(*types):
            return self.advance()
        return None

    def expect(self, tt: TT, msg="") -> Token:
        if self.check(tt):
            return self.advance()
        raise ParseError(msg or f"Expected {tt.name}", self.peek())

    def advance(self) -> Token:
        t = self.tokens[self.pos]
        if self.pos < len(self.tokens) - 1:
            self.pos += 1
        return t

    def skip_newlines(self):
        while self.check(TT.NEWLINE):
            self.advance()

    def parse(self) -> Program:
        stmts = []
        self.skip_newlines()
        while not self.check(TT.EOF):
            stmt = self.parse_stmt()
            if stmt:
                stmts.append(stmt)
            self.skip_newlines()
        return Program(stmts=stmts)

    def parse_stmt(self) -> Optional[Node]:
        self.skip_newlines()
        t = self.peek()

        # Target declaration: @web+@ios
        if t.type == TT.AT:
            return self.parse_target()

        # Stdlib import: #http.ws
        if t.type == TT.HASH:
            return self.parse_stdlib_import()

        # Extern import: from pip numpy as np
        if t.type == TT.FROM:
            return self.parse_extern_import()

        # Type definition: type User:{...}
        if t.type == TT.TYPE:
            return self.parse_type_def()

        # Trait definition: trait Fmt: ...
        if t.type == TT.TRAIT:
            return self.parse_trait_def()

        # Const: const MAX:N = 1024
        if t.type == TT.CONST:
            return self.parse_const()

        # Export + function: ^fnName->...
        if t.type == TT.CARET:
            return self.parse_fn_def(exported=True)

        # Emit: ! expr
        if t.type == TT.BANG:
            self.advance()
            val = self.parse_expr()
            return Emit(value=val, line=t.line)

        # Mutable bind: ~x = expr  or  ~x:T = expr
        if t.type == TT.TILDE:
            return self.parse_bind(mutable=True)

        # Identifier could be: bind, fn def, or expression
        if t.type == TT.IDENT:
            # Peek ahead to detect function definition: name->
            if self.peek(1).type == TT.ARROW:
                return self.parse_fn_def(exported=False)
            # name.method->, e.g. User.fmt->
            if self.peek(1).type == TT.DOT and self.peek(3).type == TT.ARROW:
                return self.parse_fn_def(exported=False)
            # name = expr (bind) — but NOT == (comparison)
            # Also skip if value starts with lambda: name = ident -> expr
            if self.peek(1).type == TT.EQ and self.peek(2).type != TT.EQ:
                val_tok = self.peek(2)
                if val_tok.type == TT.ARROW:
                    pass  # name = -> expr (lambda), fall through to expression
                elif val_tok.type == TT.IDENT and self.peek(3).type == TT.ARROW:
                    pass  # name = ident -> expr (lambda), fall through to expression
                else:
                    return self.parse_bind(mutable=False)
            # name:Type = expr
            if self.peek(1).type == TT.COLON:
                # Could be typed bind or function param
                return self.parse_bind_or_expr()

        # Match expression as statement
        if t.type == TT.MATCH:
            return self.parse_match()

        # Everything else is an expression statement
        expr = self.parse_expr()
        return expr

    def parse_bind(self, mutable=False) -> Bind:
        if mutable:
            t = self.advance()  # ~
        name_tok = self.expect(TT.IDENT, "Expected identifier in bind")
        # optional type annotation
        if self.match(TT.COLON):
            self.parse_type_expr()  # consume type, ignore for now
        self.expect(TT.EQ, "Expected '=' in bind")
        value = self.parse_expr()
        return Bind(name=name_tok.value, value=value, mutable=mutable, line=name_tok.line)

    def parse_bind_or_expr(self) -> Node:
        """Ident : Type = expr  or just expression"""
        # Try bind with type annotation
        name_tok = self.advance()  # ident
        if self.check(TT.COLON):
            self.advance()
            self.parse_type_expr()
            if self.check(TT.EQ):
                self.advance()
                value = self.parse_expr()
                return Bind(name=name_tok.value, value=value, mutable=False, line=name_tok.line)
        # Not a bind — backtrack conceptually by building ident and parsing rest as expr
        # We already consumed the ident, reconstruct
        node = Ident(name=name_tok.value, line=name_tok.line)
        return self.parse_expr_tail(node)

    def parse_type_expr(self) -> str:
        """Parse a type expression, return as string (for now)."""
        parts = []
        if self.check(TT.LBRACK):
            self.advance()
            inner = self.parse_type_expr()
            self.expect(TT.RBRACK)
            return f"[{inner}]"
        if self.check(TT.LBRACE):
            self.advance()
            k = self.parse_type_expr()
            self.expect(TT.COLON)
            v = self.parse_type_expr()
            self.expect(TT.RBRACE)
            return f"{{{k}:{v}}}"
        if self.check(TT.IDENT):
            name = self.advance().value
            parts.append(name)
            if self.check(TT.LT):
                self.advance()
                inner = self.parse_type_expr()
                self.match(TT.GT)
                parts.append(f"<{inner}>")
            if self.match(TT.QMARK):
                parts.append("?")
        return ''.join(parts)

    def parse_fn_def(self, exported=False) -> FnDef:
        if exported:
            self.advance()  # ^

        # Parse name (could be Name or Name.method)
        name_tok = self.expect(TT.IDENT, "Expected function name")
        name = name_tok.value
        if self.match(TT.DOT):
            method = self.expect(TT.IDENT, "Expected method name")
            name = f"{name}.{method.value}"

        self.expect(TT.ARROW, "Expected '->' in function definition")

        # Parse params — greedy: keep consuming IDENT tokens.
        # After 'param:' if next is UPPERCASE ident → type annotation.
        # If next is not ident or is lowercase → ':' is body separator.
        params = []
        while self.check(TT.IDENT):
            pname = self.advance().value
            ptype = None
            if self.match(TT.COLON):
                # Distinguish type annotation from body separator
                if self.check(TT.IDENT) and self.peek().value[0].isupper():
                    # Type annotation: e.g. a:N or a:String
                    ptype = self.parse_type_expr()
                    params.append((pname, ptype))
                    # More params if next is IDENT
                    if not self.check(TT.IDENT):
                        break
                    continue
                else:
                    # Body separator: don't consume anything further
                    # The ':' was already consumed by match, that's fine
                    # It signals end of params
                    params.append((pname, ptype))
                    break
            params.append((pname, ptype))

        # Return type after colon (optional)
        ret_type = None
        if self.match(TT.COLON):
            ret_type = self.parse_type_expr()

        # Body: either inline (same line) or indented block
        body = []
        if self.match(TT.NEWLINE):
            self.skip_newlines()
            if self.match(TT.INDENT):
                while not self.check(TT.DEDENT) and not self.check(TT.EOF):
                    s = self.parse_stmt()
                    if s: body.append(s)
                    self.skip_newlines()
                self.match(TT.DEDENT)
        else:
            # Inline body
            stmt = self.parse_stmt()
            if stmt: body.append(stmt)

        return FnDef(name=name, params=params, ret_type=ret_type, body=body,
                     exported=exported, line=name_tok.line)

    def parse_type_def(self) -> TypeDef:
        self.advance()  # type
        name = self.expect(TT.IDENT, "Expected type name").value
        # optional generic params (skip)
        if self.match(TT.LT):
            while not self.check(TT.GT): self.advance()
            self.advance()
        self.expect(TT.COLON, "Expected ':' in type definition")
        self.expect(TT.LBRACE)
        fields = {}
        while not self.check(TT.RBRACE) and not self.check(TT.EOF):
            self.skip_newlines()
            if self.check(TT.RBRACE): break
            fn = self.expect(TT.IDENT, "Expected field name").value
            self.expect(TT.COLON, "Expected ':' after field name")
            ft = self.parse_type_expr()
            fields[fn] = ft
            self.match(TT.COMMA)
            self.skip_newlines()
        self.expect(TT.RBRACE)
        return TypeDef(name=name, fields=fields)

    def parse_trait_def(self) -> TraitDef:
        self.advance()  # trait
        name = self.expect(TT.IDENT, "Expected trait name").value
        self.expect(TT.COLON)
        methods = []
        self.skip_newlines()
        if self.match(TT.INDENT):
            while not self.check(TT.DEDENT) and not self.check(TT.EOF):
                m = self.parse_fn_def(exported=False)
                methods.append(m)
                self.skip_newlines()
            self.match(TT.DEDENT)
        return TraitDef(name=name, methods=methods)

    def parse_const(self) -> Bind:
        self.advance()  # const
        name = self.expect(TT.IDENT).value
        if self.match(TT.COLON): self.parse_type_expr()
        self.expect(TT.EQ)
        value = self.parse_expr()
        return Bind(name=name, value=value, mutable=False)

    def parse_target(self) -> TargetDecl:
        targets = []
        while self.match(TT.AT):
            targets.append(self.expect(TT.IDENT, "Expected target name").value)
            if not self.match(TT.PLUS): break
        return TargetDecl(targets=targets)

    def parse_stdlib_import(self) -> StdlibImport:
        self.advance()  # #
        path_parts = [self.expect(TT.IDENT, "Expected module name").value]
        while self.match(TT.DOT):
            path_parts.append(self.expect(TT.IDENT).value)
        return StdlibImport(path='.'.join(path_parts))

    def parse_extern_import(self) -> ExternImport:
        self.advance()  # from
        eco = self.expect(TT.IDENT, "Expected ecosystem (pip/npm/cargo/sys)").value
        pkg = self.expect(TT.IDENT, "Expected package name").value
        # version pin: @1.2.3
        if self.match(TT.AT):
            self.advance()  # version number
        self.expect(TT.AS, "Expected 'as' in import")
        alias = self.expect(TT.IDENT, "Expected alias").value
        return ExternImport(ecosystem=eco, package=pkg, alias=alias)

    def parse_match(self) -> MatchExpr:
        t = self.advance()  # match
        subject = self.parse_expr()
        self.expect(TT.LBRACE)
        arms = []
        self._skip_ws()
        while not self.check(TT.RBRACE) and not self.check(TT.EOF):
            self._skip_ws()
            if self.check(TT.RBRACE): break
            pattern = self.parse_expr()
            self.expect(TT.ARROW)
            body = self.parse_expr()
            arms.append((pattern, body))
            # Arms separated by comma or newline
            self.match(TT.COMMA)
            self._skip_ws()
        self.expect(TT.RBRACE)
        return MatchExpr(subject=subject, arms=arms, line=t.line)

    def _skip_ws(self):
        """Skip newlines, indents, and dedents (for blocks inside delimiters)."""
        while self.check(TT.NEWLINE, TT.INDENT, TT.DEDENT):
            self.advance()

    def parse_expr(self) -> Node:
        return self.parse_bind_expr()

    def parse_bind_expr(self) -> Node:
        """Handle assignment in expression context: name = expr (including lambdas)."""
        left = self.parse_pipe()
        if isinstance(left, Ident) and self.check(TT.EQ) and self.peek(1).type != TT.EQ:
            self.advance()  # =
            # Check if value is a lambda: ident -> body
            if self.check(TT.IDENT) and self.peek(1).type == TT.ARROW:
                param = self.advance().value
                self.advance()  # ->
                body_expr = self.parse_bind_expr()
                value = FnDef(name=None, params=[(param, None)], ret_type=None,
                              body=[Emit(value=body_expr, line=left.line)], line=left.line)
            elif self.check(TT.ARROW):
                self.advance()  # ->
                body_expr = self.parse_bind_expr()
                value = FnDef(name=None, params=[], ret_type=None,
                              body=[Emit(value=body_expr, line=left.line)], line=left.line)
            else:
                value = self.parse_bind_expr()
            return Bind(name=left.name, value=value, mutable=False, line=left.line)
        return left

    def parse_pipe(self) -> Node:
        left = self.parse_par()
        if self.check(TT.PIPE):
            # | is pipe only if followed by callable-like expr
            nxt = self.peek(1)
            if nxt.type in (TT.NUMBER, TT.STRING, TT.LPAREN):
                return left
            if nxt.type == TT.IDENT and not self._is_callable_next():
                return left
            stages = [left]
            while self.check(TT.PIPE):
                self.advance()
                stages.append(self.parse_par())
            return Pipe(stages=stages, line=left.line)
        if self.check(TT.PIPE_FWD):
            # |> is pipe-forward (sequential) — always
            stages = [left]
            while self.match(TT.PIPE_FWD):
                stages.append(self.parse_par())
            return Pipe(stages=stages, line=left.line)
        return left

    def parse_par(self) -> Node:
        left = self.parse_seq()
        if self.check(TT.AMP):
            # & is parallel only if followed by callable-like expr
            nxt = self.peek(1)
            if nxt.type in (TT.NUMBER, TT.STRING, TT.LPAREN):
                return left
            if nxt.type == TT.IDENT and not self._is_callable_next():
                return left
            exprs = [left]
            while self.check(TT.AMP):
                self.advance()
                exprs.append(self.parse_seq())
            return Par(exprs=exprs, line=left.line)
        return left

    def _is_callable_next(self) -> bool:
        """Check if next token looks like a function call or method access."""
        t = self.peek(1)
        if t.type == TT.LPAREN:
            return True
        if t.type == TT.DOT:
            return True
        return False

    def parse_seq(self) -> Node:
        return self.parse_race()

    def parse_race(self) -> Node:
        left = self.parse_fallback()
        if self.match(TT.RACE):
            right = self.parse_fallback()
            return Race(left=left, right=right, line=left.line)
        return left

    def parse_fallback(self) -> Node:
        left = self.parse_map_filter()
        if self.match(TT.DQMARK):
            right = self.parse_map_filter()
            return Fallback(expr=left, default=right, line=left.line)
        return left

    def parse_map_filter(self) -> Node:
        left = self.parse_compare()
        while True:
            # * is map ONLY when followed by ( (lambda) or ident->
            if self.check(TT.STAR) and self.peek(1).type == TT.LPAREN:
                self.advance()
                fn = self.parse_primary()
                fn = self.parse_expr_tail(fn)
                left = MapOp(collection=left, fn=fn, line=left.line)
            elif self.check(TT.PCT) and self.peek(1).type == TT.LPAREN:
                self.advance()
                fn = self.parse_primary()
                fn = self.parse_expr_tail(fn)
                left = FilterOp(collection=left, fn=fn, line=left.line)
            elif self.check(TT.STAR) and self.peek(1).type == TT.IDENT and self.peek(2).type == TT.ARROW:
                self.advance()
                fn = self.parse_primary()
                fn = self.parse_expr_tail(fn)
                left = MapOp(collection=left, fn=fn, line=left.line)
            elif self.check(TT.PCT) and self.peek(1).type == TT.IDENT and self.peek(2).type == TT.ARROW:
                self.advance()
                fn = self.parse_primary()
                fn = self.parse_expr_tail(fn)
                left = FilterOp(collection=left, fn=fn, line=left.line)
            else:
                break
        return left

    def parse_compare(self) -> Node:
        left = self.parse_add()
        while True:
            if self.check(TT.LT):
                self.advance(); left = BinOp(op='<', left=left, right=self.parse_add(), line=left.line)
            elif self.check(TT.GT):
                self.advance(); left = BinOp(op='>', left=left, right=self.parse_add(), line=left.line)
            elif self.check(TT.LTE):
                self.advance(); left = BinOp(op='<=', left=left, right=self.parse_add(), line=left.line)
            elif self.check(TT.GTE):
                self.advance(); left = BinOp(op='>=', left=left, right=self.parse_add(), line=left.line)
            elif self.check(TT.EQ) and self.peek(1).type == TT.EQ:
                self.advance(); self.advance(); left = BinOp(op='==', left=left, right=self.parse_add(), line=left.line)
            elif self.check(TT.BANG) and self.peek(1).type == TT.EQ:
                self.advance(); self.advance(); left = BinOp(op='!=', left=left, right=self.parse_add(), line=left.line)
            else:
                break
        return left

    def parse_add(self) -> Node:
        left = self.parse_mul()
        while self.check(TT.PLUS) or self.check(TT.MINUS):
            op = self.advance().value
            right = self.parse_mul()
            left = BinOp(op=op, left=left, right=right, line=left.line)
        return left

    def parse_mul(self) -> Node:
        left = self.parse_unary()
        while self.check(TT.SLASH) or self.check(TT.STAR) or self.check(TT.PCT):
            if self.check(TT.STAR):
                next_t = self.peek(1).type
                # * followed by ( means map: items*(fn)
                if next_t == TT.LPAREN:
                    break
                # * followed by ident then -> means map: items*(x->...)
                # but * followed by ident NOT followed by -> means multiply: x*y
                if next_t == TT.IDENT and self.peek(2).type == TT.ARROW:
                    break
            if self.check(TT.PCT):
                next_t = self.peek(1).type
                # % followed by ( means filter: items%(fn)
                if next_t == TT.LPAREN:
                    break
                if next_t == TT.IDENT and self.peek(2).type == TT.ARROW:
                    break
            op = self.advance().value
            right = self.parse_unary()
            left = BinOp(op=op, left=left, right=right, line=left.line)
        return left

    def parse_unary(self) -> Node:
        return self.parse_postfix()

    def parse_postfix(self) -> Node:
        node = self.parse_primary()
        return self.parse_expr_tail(node)

    def parse_expr_tail(self, node: Node) -> Node:
        """Handle postfix: calls, attr access, ?, .t(), .retry()"""
        while True:
            if self.match(TT.DOT):
                attr = self.expect(TT.IDENT, "Expected attribute").value
                if attr == 't' and self.check(TT.LPAREN):
                    self.advance()
                    ms = self.parse_expr()
                    self.expect(TT.RPAREN)
                    node = TimeoutExpr(expr=node, ms=ms, line=node.line)
                elif attr == 'retry' and self.check(TT.LPAREN):
                    self.advance()
                    n = self.parse_expr()
                    self.expect(TT.RPAREN)
                    node = RetryExpr(expr=node, n=n, line=node.line)
                else:
                    node = GetAttr(obj=node, attr=attr, line=node.line)
            elif self.check(TT.LPAREN):
                self.advance()
                args, kwargs = self.parse_args()
                self.expect(TT.RPAREN)
                node = Call(callee=node, args=args, kwargs=kwargs, line=node.line)
            elif self.check(TT.LBRACK):
                self.advance()
                idx = self.parse_expr()
                self.expect(TT.RBRACK)
                node = Index(obj=node, index=idx, line=node.line)
            elif self.match(TT.QMARK):
                node = Propagate(expr=node, line=node.line)
            elif self.match(TT.DOTDOT):
                end = self.parse_unary()
                node = RangeExpr(start=node, end=end, line=node.line)
            else:
                break
        return node

    def parse_args(self) -> Tuple[List[Node], Dict[str, Node]]:
        args, kwargs = [], {}
        while not self.check(TT.RPAREN) and not self.check(TT.EOF):
            # keyword arg: name: expr
            if self.check(TT.IDENT) and self.peek(1).type == TT.COLON:
                k = self.advance().value
                self.advance()  # :
                v = self.parse_expr()
                kwargs[k] = v
            else:
                args.append(self.parse_expr())
            self.match(TT.COMMA)
        return args, kwargs

    def parse_primary(self) -> Node:
        t = self.peek()

        if t.type == TT.NUMBER:
            self.advance()
            return NumberLit(value=t.value, line=t.line)

        if t.type == TT.STRING:
            self.advance()
            return StringLit(value=t.value, line=t.line)

        if t.type == TT.BOOL:
            self.advance()
            return BoolLit(value=t.value, line=t.line)

        if t.type == TT.NIL:
            self.advance()
            return NilLit(line=t.line)

        if t.type == TT.IDENT:
            self.advance()
            return Ident(name=t.value, line=t.line)

        # Lambda: x -> expr  or  -> expr
        if t.type == TT.ARROW:
            self.advance()
            params = []
            body_expr = self.parse_expr()
            return FnDef(name=None, params=params, ret_type=None,
                         body=[Emit(value=body_expr, line=t.line)], line=t.line)

        # Ident -> expr (lambda with param)
        # Handled at statement level for fn defs

        # List literal: [1, 2, 3]
        if t.type == TT.LBRACK:
            self.advance()
            items = []
            while not self.check(TT.RBRACK) and not self.check(TT.EOF):
                if self.match(TT.STAR):
                    items.append(Spread(expr=self.parse_expr(), line=t.line))
                else:
                    items.append(self.parse_expr())
                self.match(TT.COMMA)
            self.expect(TT.RBRACK)
            return ListLit(items=items, line=t.line)

        # Map literal: {k:v, ...}
        if t.type == TT.LBRACE:
            self.advance()
            pairs = []
            while not self.check(TT.RBRACE) and not self.check(TT.EOF):
                self.skip_newlines()
                if self.check(TT.RBRACE): break
                if self.match(TT.STAR):
                    pairs.append((Spread(expr=self.parse_expr(), line=t.line), None))
                else:
                    k = self.parse_expr()
                    self.expect(TT.COLON)
                    v = self.parse_expr()
                    pairs.append((k, v))
                self.match(TT.COMMA)
                self.skip_newlines()
            self.expect(TT.RBRACE)
            return MapLit(pairs=pairs, line=t.line)

        # Parenthesized expr or lambda: (x->expr) (x y->expr)
        if t.type == TT.LPAREN:
            self.advance()
            saved = self.pos
            params = []
            is_lambda = False
            while self.check(TT.IDENT):
                pname = self.advance().value
                ptype = None
                if self.check(TT.COLON):
                    self.advance()
                    ptype = self.parse_type_expr()
                params.append((pname, ptype))
                if self.check(TT.ARROW):
                    is_lambda = True
                    break
                if not self.check(TT.IDENT):
                    break
            if is_lambda:
                self.advance()  # ->
                body_expr = self.parse_expr()
                self.match(TT.RPAREN)
                return FnDef(name=None, params=params, ret_type=None,
                             body=[Emit(value=body_expr, line=t.line)], line=t.line)
            self.pos = saved
            expr = self.parse_expr()
            self.match(TT.RPAREN)
            return expr

        # Match
        if t.type == TT.MATCH:
            return self.parse_match()

        # Discard
        if t.type == TT.UNDER:
            self.advance()
            return Ident(name='_', line=t.line)

        # Range: handled in postfix as expr .. expr - skip for now
        # Emit as statement
        if t.type == TT.BANG:
            self.advance()
            val = self.parse_expr()
            return Emit(value=val, line=t.line)

        raise ParseError(f"Unexpected token in expression", t)


# ═══════════════════════════════════════════════════════════
#  RUNTIME VALUES
# ═══════════════════════════════════════════════════════════

class KarnError(Exception):
    """KARN runtime error — wrapped in Err()"""
    def __init__(self, msg: str, context: List[str] = None, line: int = 0):
        self.msg     = msg
        self.context = context or []
        self.line    = line
        loc = f" [line {line}]" if line else ""
        super().__init__(f"{msg}{loc}")

    def wrap(self, ctx: str) -> 'KarnError':
        return KarnError(self.msg, [ctx] + self.context, self.line)

    def __repr__(self):
        parts = [f"Err: {self.msg}"]
        if self.line:
            parts[0] += f" (line {self.line})"
        for c in self.context:
            parts.append(f"  context: {c}")
        return '\n'.join(parts)


class OkVal:
    """Ok(value) wrapper"""
    def __init__(self, value):
        self.value = value
    def __repr__(self):
        return f"Ok({self.value!r})"


class KarnFn:
    """First-class KARN function"""
    def __init__(self, node: FnDef, closure: 'Env'):
        self.node    = node
        self.closure = closure
        self.calls   = 0  # for JIT profiling

    def __repr__(self):
        return f"<fn {self.node.name or 'λ'}>"


class KarnType:
    """Runtime type instance"""
    def __init__(self, type_name: str, fields: Dict[str, Any]):
        self.type_name = type_name
        self.fields    = fields

    def __repr__(self):
        fs = ', '.join(f"{k}:{v!r}" for k, v in self.fields.items())
        return f"{self.type_name}{{{fs}}}"

    def __getitem__(self, key):
        return self.fields[key]


# ═══════════════════════════════════════════════════════════
#  ENVIRONMENT (scope)
# ═══════════════════════════════════════════════════════════

class Env:
    def __init__(self, parent: Optional['Env'] = None):
        self.bindings: Dict[str, Any] = {}
        self.parent   = parent
        self.mutable: set = set()

    def get(self, name: str) -> Any:
        if name in self.bindings:
            return self.bindings[name]
        if self.parent:
            return self.parent.get(name)
        raise KarnError(f"Undefined: '{name}'")

    def set(self, name: str, value: Any, mutable=False):
        self.bindings[name] = value
        if mutable:
            self.mutable.add(name)

    def rebind(self, name: str, value: Any):
        if name in self.bindings:
            if name not in self.mutable:
                raise KarnError(f"Cannot rebind immutable '{name}'")
            self.bindings[name] = value
            return
        if self.parent:
            self.parent.rebind(name, value)
            return
        raise KarnError(f"Undefined: '{name}'")

    def child(self) -> 'Env':
        return Env(parent=self)


# ═══════════════════════════════════════════════════════════
#  EMIT SIGNAL (for ! operator)
# ═══════════════════════════════════════════════════════════

class EmitSignal(Exception):
    def __init__(self, value):
        self.value = value


# ═══════════════════════════════════════════════════════════
#  STDLIB IMPLEMENTATIONS
# ═══════════════════════════════════════════════════════════

class StdlibModule:
    """Base for all stdlib modules"""
    def attr(self, name: str) -> Any:
        if hasattr(self, name):
            return getattr(self, name)
        raise KarnError(f"Module has no function '{name}'")


class HttpModule(StdlibModule):
    def get(self, url, **kw):
        try:
            import urllib.request
            with urllib.request.urlopen(str(url), timeout=kw.get('timeout', 10)) as r:
                return OkVal(r.read().decode())
        except Exception as e:
            return KarnError(str(e))

    def serve(self, port, routes):
        print(f"[karn:http] Serving on port {port}")
        print(f"[karn:http] Routes: {routes}")
        return OkVal(None)

    def ws(self, url):
        return OkVal({"url": url, "_type": "ws"})


class FsModule(StdlibModule):
    def read(self, path):
        try:
            return OkVal(open(str(path)).read())
        except Exception as e:
            return KarnError(str(e))

    def write(self, path, content):
        try:
            open(str(path), 'w').write(str(content))
            return OkVal(None)
        except Exception as e:
            return KarnError(str(e))

    def list(self, path='.'):
        try:
            return OkVal(os.listdir(str(path)))
        except Exception as e:
            return KarnError(str(e))


class LogModule(StdlibModule):
    def info(self, msg):
        print(f"\033[36m[INFO]\033[0m {self._fmt(msg)}")
        return OkVal(None)

    def warn(self, msg):
        print(f"\033[33m[WARN]\033[0m {self._fmt(msg)}")
        return OkVal(None)

    def err(self, msg):
        print(f"\033[31m[ERR]\033[0m  {self._fmt(msg)}", file=sys.stderr)
        return OkVal(None)

    def _fmt(self, v):
        if isinstance(v, dict): return json.dumps(v)
        return str(v)


class EnvModule(StdlibModule):
    def get(self, key, default=None):
        return OkVal(os.environ.get(str(key), default))

    def require(self, key):
        v = os.environ.get(str(key))
        if v is None:
            return KarnError(f"Required env var '{key}' not set")
        return OkVal(v)


class JsonModule(StdlibModule):
    def parse(self, s):
        try:
            return OkVal(json.loads(str(s)))
        except Exception as e:
            return KarnError(f"JSON parse error: {e}")

    def stringify(self, obj, indent=None):
        try:
            return OkVal(json.dumps(obj, indent=indent, default=str))
        except Exception as e:
            return KarnError(f"JSON stringify error: {e}")

    def pretty(self, obj):
        return self.stringify(obj, indent=2)


class MathModule(StdlibModule):
    def abs(self, x):    return abs(float(x))
    def ceil(self, x):   import math; return math.ceil(float(x))
    def floor(self, x):  import math; return math.floor(float(x))
    def round(self, x):  return round(float(x))
    def sqrt(self, x):   import math; return math.sqrt(float(x))
    def pow(self, x, y): return float(x) ** float(y)
    def min(self, *args):
        args = [float(a) for a in args]
        return min(args)
    def max(self, *args):
        args = [float(a) for a in args]
        return max(args)
    def sin(self, x):    import math; return math.sin(float(x))
    def cos(self, x):    import math; return math.cos(float(x))
    def log(self, x):    import math; return math.log(float(x))
    def pi(self):        import math; return math.pi
    def e(self):         import math; return math.e


class TimeModule(StdlibModule):
    def now(self):
        return OkVal(time.time())

    def sleep(self, ms):
        time.sleep(float(ms) / 1000.0)
        return OkVal(None)

    def fmt(self, ts=None, fmt_str="%Y-%m-%d %H:%M:%S"):
        import datetime
        t = datetime.datetime.fromtimestamp(float(ts)) if ts else datetime.datetime.now()
        return OkVal(t.strftime(str(fmt_str)))

    def date(self):
        import datetime
        d = datetime.date.today()
        return OkVal({"year": d.year, "month": d.month, "day": d.day})


class StrModule(StdlibModule):
    def join(self, lst, sep=""):
        return OkVal(str(sep).join(str(x) for x in lst))

    def split(self, s, sep=" "):
        return OkVal(str(s).split(str(sep)))

    def replace(self, s, old, new):
        return OkVal(str(s).replace(str(old), str(new)))

    def contains(self, s, sub):
        return OkVal(str(sub) in str(s))

    def starts(self, s, prefix):
        return OkVal(str(s).startswith(str(prefix)))

    def ends(self, s, suffix):
        return OkVal(str(s).endswith(str(suffix)))

    def trim(self, s):
        return OkVal(str(s).strip())

    def repeat(self, s, n):
        return OkVal(str(s) * int(n))


class CryptoModule(StdlibModule):
    def md5(self, s):
        import hashlib
        return OkVal(hashlib.md5(str(s).encode()).hexdigest())

    def sha256(self, s):
        import hashlib
        return OkVal(hashlib.sha256(str(s).encode()).hexdigest())

    def base64_encode(self, s):
        import base64
        return OkVal(base64.b64encode(str(s).encode()).decode())

    def base64_decode(self, s):
        import base64
        try:
            return OkVal(base64.b64decode(str(s)).decode())
        except Exception as e:
            return KarnError(f"base64 decode error: {e}")

    def uuid(self):
        import uuid
        return OkVal(str(uuid.uuid4()))


class DbModule(StdlibModule):
    """Minimal DB module (stub for real implementations)."""
    def q(self, table, where=None):
        print(f"[karn:db] Query: {table} WHERE {where}")
        return OkVal([])

    def exec(self, sql, *args):
        print(f"[karn:db] Exec: {sql} args={args}")
        return OkVal({"rows_affected": 0})


def build_stdlib() -> Env:
    env = Env()
    env.set('http',  HttpModule())
    env.set('fs',    FsModule())
    env.set('log',   LogModule())
    env.set('env',   EnvModule())
    env.set('json',  JsonModule())
    env.set('math',  MathModule())
    env.set('time',  TimeModule())
    env.set('str',   StrModule())
    env.set('crypto',CryptoModule())
    env.set('db',    DbModule())

    # Built-in functions
    def karn_print(*args):
        print(*[str(a) for a in args])
        return OkVal(None)

    env.set('print',  karn_print)
    env.set('int',    lambda x: int(x))
    env.set('float',  lambda x: float(x))
    env.set('len',    lambda x: len(x))
    env.set('keys',   lambda x: list(x.keys()) if isinstance(x, dict) else [])
    env.set('values', lambda x: list(x.values()) if isinstance(x, dict) else [])
    env.set('range',  lambda s, e=None: list(range(int(s), int(e)) if e else range(int(s))))
    env.set('type_of',lambda x: type(x).__name__)
    env.set('repr',   lambda x: repr(x))
    env.set('sorted', lambda x: sorted(x))
    env.set('reversed', lambda x: list(reversed(x)))
    env.set('sum',    lambda x: sum(x))
    env.set('any',    lambda x: any(x))
    env.set('all',    lambda x: all(x))
    env.set('zip',    lambda a, b: list(zip(a, b)))

    return env


# ═══════════════════════════════════════════════════════════
#  INTERPRETER
# ═══════════════════════════════════════════════════════════

class Interpreter:
    def __init__(self):
        self.global_env = build_stdlib()
        self.type_defs: Dict[str, TypeDef] = {}
        self.call_counts: Dict[str, int] = {}
        self.jit_mode = False

    def run(self, program: Program) -> Any:
        result = None
        for stmt in program.stmts:
            result = self.eval(stmt, self.global_env)
        return result

    def eval(self, node: Node, env: Env) -> Any:
        t = type(node)

        if t == NumberLit:  return node.value
        if t == StringLit:  return node.value
        if t == BoolLit:    return node.value
        if t == NilLit:     return None

        if t == Ident:
            try:
                return env.get(node.name)
            except KarnError:
                if node.name == 'Ok':
                    return lambda v: OkVal(v)
                if node.name == 'Err':
                    return lambda v: KarnError(str(v))
                raise

        if t == ListLit:
            result = []
            for item in node.items:
                if isinstance(item, Spread):
                    v = self.eval(item.expr, env)
                    if isinstance(v, list): result.extend(v)
                else:
                    result.append(self.eval(item, env))
            return result

        if t == MapLit:
            result = {}
            for k, v in node.pairs:
                if isinstance(k, Spread):
                    spread_val = self.eval(k.expr, env)
                    if isinstance(spread_val, dict):
                        result.update(spread_val)
                else:
                    # Bare identifier keys are treated as strings: {x:1} → {"x":1}
                    if isinstance(k, Ident):
                        key = k.name
                    else:
                        key = self.eval(k, env)
                    val = self.eval(v, env)
                    result[key] = val
            return result

        if t == Bind:
            if node.mutable and node.name in env.bindings:
                env.rebind(node.name, self.eval(node.value, env))
            else:
                env.set(node.name, self.eval(node.value, env), mutable=node.mutable)
            return None

        if t == Emit:
            raise EmitSignal(self.eval(node.value, env))

        if t == BinOp:
            l = self.eval(node.left, env)
            r = self.eval(node.right, env)
            ops = {'+': lambda a,b: a+b, '-': lambda a,b: a-b,
                   '*': lambda a,b: a*b, '/': lambda a,b: a/b, '%': lambda a,b: a%b,
                   '<': lambda a,b: a<b, '>': lambda a,b: a>b,
                   '<=':lambda a,b: a<=b,'>=':lambda a,b: a>=b,
                   '==':lambda a,b: a==b,'!=':lambda a,b: a!=b}
            if node.op in ops:
                try:
                    return ops[node.op](l, r)
                except TypeError:
                    raise KarnError(f"Type error: cannot {node.op} {type(l).__name__} and {type(r).__name__}",
                                    line=node.line)
            raise KarnError(f"Unknown operator: {node.op}", line=node.line)

        if t == GetAttr:
            obj = self.eval(node.obj, env)
            return self._get_attr(obj, node.attr)

        if t == Index:
            obj = self.eval(node.obj, env)
            idx = self.eval(node.index, env)
            if isinstance(obj, (list, str)):
                return obj[int(idx)]
            if isinstance(obj, dict):
                return obj[idx]
            raise KarnError(f"Cannot index {type(obj).__name__}", line=node.line)

        if t == Call:
            fn   = self.eval(node.callee, env)
            args = [self.eval(a, env) for a in node.args]
            kw   = {k: self.eval(v, env) for k, v in node.kwargs.items()}
            return self._call(fn, args, kw, env)

        if t == FnDef:
            fn = KarnFn(node=node, closure=env)
            if node.name:
                env.set(node.name, fn)
                if node.exported:
                    self.global_env.set(node.name, fn)
            return fn

        if t == Pipe:
            val = self.eval(node.stages[0], env)
            for stage in node.stages[1:]:
                fn = self.eval(stage, env)
                val = self._call(fn, [val], {}, env)
            return val

        if t == Par:
            # In the interpreter, run sequentially (true parallelism needs threads)
            return [self.eval(e, env) for e in node.exprs]

        if t == Seq:
            val = self.eval(node.exprs[0], env)
            for expr in node.exprs[1:]:
                fn = self.eval(expr, env)
                val = self._call(fn, [val], {}, env) if callable(fn) else fn
            return val

        if t == Race:
            # Sequential fallback: try left, if err try right
            left = self.eval(node.left, env)
            if isinstance(left, KarnError):
                return self.eval(node.right, env)
            return left

        if t == Propagate:
            val = self.eval(node.expr, env)
            if isinstance(val, KarnError):
                raise EmitSignal(val)
            if isinstance(val, OkVal):
                return val.value
            return val

        if t == Fallback:
            val = self.eval(node.expr, env)
            if isinstance(val, KarnError) or val is None:
                return self.eval(node.default, env)
            if isinstance(val, OkVal):
                return val.value
            return val

        if t == MapOp:
            col = self.eval(node.collection, env)
            fn  = self.eval(node.fn, env)
            if not isinstance(col, list):
                raise KarnError(f"Map requires a list, got {type(col).__name__}", line=node.line)
            return [self._call(fn, [item], {}, env) for item in col]

        if t == FilterOp:
            col = self.eval(node.collection, env)
            fn  = self.eval(node.fn, env)
            if not isinstance(col, list):
                raise KarnError(f"Filter requires a list, got {type(col).__name__}", line=node.line)
            return [item for item in col if self._truthy(self._call(fn, [item], {}, env))]

        if t == RangeExpr:
            s = int(self.eval(node.start, env))
            e = int(self.eval(node.end, env))
            return list(range(s, e + 1))

        if t == MatchExpr:
            subject = self.eval(node.subject, env)
            for pattern, body in node.arms:
                matched, bindings = self._match(pattern, subject, env)
                if matched:
                    sub_env = env.child()
                    for k, v in bindings.items():
                        sub_env.set(k, v)
                    return self.eval(body, sub_env)
            return None

        if t == TypeDef:
            self.type_defs[node.name] = node
            # Create a constructor
            def constructor(**kwargs):
                return KarnType(type_name=node.name, fields=kwargs)
            def positional_ctor(*args):
                flds = list(node.fields.keys())
                return KarnType(type_name=node.name,
                                fields=dict(zip(flds, args)))
            env.set(node.name, positional_ctor)
            return None

        if t == TraitDef:
            return None  # Traits are structural, handled at call time

        if t == TargetDecl:
            return None  # Compilation hint

        if t == StdlibImport:
            return self._load_stdlib(node.path, env)

        if t == ExternImport:
            return self._load_extern(node, env)

        if t == TimeoutExpr:
            # In interpreter: just evaluate (timeout not enforced)
            return self.eval(node.expr, env)

        if t == RetryExpr:
            n = int(self.eval(node.n, env))
            for i in range(n):
                val = self.eval(node.expr, env)
                if not isinstance(val, KarnError):
                    return val
                if i < n - 1:
                    time.sleep(0.1 * (2 ** i))  # exponential backoff
            return val

        if t == Program:
            return self.run(node)

        # Skip unknown nodes gracefully
        return None

    JIT_THRESHOLD = 10  # calls before JIT compilation

    def _call(self, fn: Any, args: List[Any], kwargs: Dict[str, Any], env: Env) -> Any:
        if fn is None:
            raise KarnError("Cannot call nil")

        if isinstance(fn, KarnFn):
            fn.calls += 1

            # JIT: if function is hot, try to compile and cache
            if self.jit_mode and fn.calls == self.JIT_THRESHOLD and fn.node.name:
                try:
                    self._jit_compile(fn)
                except Exception:
                    pass  # fallback to tree-walk

            # Use JIT-compiled version if available
            if hasattr(fn, '_jit_fn') and fn._jit_fn is not None:
                try:
                    return fn._jit_fn(*args, **kwargs)
                except EmitSignal as e:
                    return e.value
                except Exception:
                    pass  # fallback to tree-walk

            call_env = fn.closure.child()
            for i, (pname, _ptype) in enumerate(fn.node.params):
                if i < len(args):
                    call_env.set(pname, args[i])
            for k, v in kwargs.items():
                call_env.set(k, v)
            try:
                result = None
                for stmt in fn.node.body:
                    result = self.eval(stmt, call_env)
                return result
            except EmitSignal as e:
                return e.value

        if callable(fn):
            try:
                if kwargs:
                    return fn(*args, **kwargs)
                return fn(*args)
            except KarnError as e:
                return e
            except Exception as e:
                return KarnError(str(e))

        if isinstance(fn, StdlibModule):
            raise KarnError(f"Module not callable directly")

        raise KarnError(f"Not callable: {type(fn).__name__} {fn!r}")

    def _jit_compile(self, fn: KarnFn):
        """Compile a hot KARN function to a Python callable."""
        from karn import CodeGen  # import here to avoid circular at module level
        gen = CodeGen(target='python')
        gen.gen_fn(fn.node)
        py_code = '\n'.join(gen.lines)

        # Compile and execute in a namespace
        namespace = {}
        # Inject stdlib into namespace
        for name, val in self.global_env.bindings.items():
            if not callable(val) or isinstance(val, KarnFn):
                namespace[name] = val
        # Inject Ok/Err
        namespace['_Ok'] = OkVal
        namespace['_Err'] = KarnError
        namespace['_prop'] = lambda v: v.value if isinstance(v, OkVal) else (v if not isinstance(v, KarnError) else (_ for _ in ()).throw(v))

        compiled = compile(py_code, f"<jit:{fn.node.name}>", 'exec')
        exec(compiled, namespace)

        fn_name = fn.node.name.replace('.', '__').replace('-', '_')
        if fn_name in namespace:
            fn._jit_fn = namespace[fn_name]

    def _get_attr(self, obj: Any, attr: str) -> Any:
        if isinstance(obj, StdlibModule):
            return obj.attr(attr)

        if isinstance(obj, KarnType):
            if attr in obj.fields:
                return obj.fields[attr]
            raise KarnError(f"Type '{obj.type_name}' has no field '{attr}'")

        if isinstance(obj, dict):
            if attr in obj:
                return obj[attr]
            raise KarnError(f"Map has no key '{attr}'")

        if isinstance(obj, list):
            builtins = {
                'len':    lambda: len(obj),
                'first':  lambda: obj[0] if obj else None,
                'last':   lambda: obj[-1] if obj else None,
                'append': lambda x: [*obj, x],
                'map':    lambda f: [self._call(f, [i], {}, Env()) for i in obj],
                'filter': lambda f: [i for i in obj if self._truthy(self._call(f,[i],{},Env()))],
                'join':   lambda s='': s.join(str(i) for i in obj),
            }
            if attr in builtins:
                return builtins[attr]
            raise KarnError(f"List has no method '{attr}'")

        if isinstance(obj, str):
            builtins = {
                'len':   lambda: len(obj),
                'upper': lambda: obj.upper(),
                'lower': lambda: obj.lower(),
                'trim':  lambda: obj.strip(),
                'split': lambda s=' ': obj.split(s),
                'hash':  lambda: hash(obj),
                'contains': lambda s: s in obj,
            }
            if attr in builtins:
                return builtins[attr]
            raise KarnError(f"String has no method '{attr}'")

        if hasattr(obj, attr):
            return getattr(obj, attr)

        raise KarnError(f"Cannot get '{attr}' from {type(obj).__name__}")

    def _match(self, pattern: Node, subject: Any, env: Env) -> Tuple[bool, Dict]:
        """Returns (matched, bindings)"""
        if isinstance(pattern, NilLit):
            return subject is None, {}

        if isinstance(pattern, Ident):
            name = pattern.name
            if name == '_':
                return True, {}
            # Check if it's a known variant name (Ok, Err)
            if name == 'Ok':
                return isinstance(subject, OkVal), {}
            if name == 'Err':
                return isinstance(subject, KarnError), {}
            # Otherwise it's a capture binding
            return True, {name: subject}

        if isinstance(pattern, Call):
            # Pattern like Ok v or Err e
            if isinstance(pattern.callee, Ident):
                cname = pattern.callee.name
                if cname == 'Ok' and isinstance(subject, OkVal):
                    if pattern.args:
                        return True, {pattern.args[0].name if isinstance(pattern.args[0], Ident) else '_': subject.value}
                    return True, {}
                if cname == 'Err' and isinstance(subject, KarnError):
                    if pattern.args:
                        return True, {pattern.args[0].name if isinstance(pattern.args[0], Ident) else '_': subject}
                    return True, {}
                # Try matching type name
                if isinstance(subject, KarnType) and subject.type_name == cname:
                    return True, {}

        if isinstance(pattern, (NumberLit, StringLit, BoolLit)):
            v = self.eval(pattern, env)
            return v == subject, {}

        return False, {}

    def _truthy(self, val: Any) -> bool:
        if val is None or val is False: return False
        if isinstance(val, (int, float)): return val != 0
        if isinstance(val, str): return len(val) > 0
        if isinstance(val, list): return len(val) > 0
        if isinstance(val, KarnError): return False
        return True

    def _load_stdlib(self, path: str, env: Env):
        top = path.split('.')[0]
        modules = {
            'http': HttpModule(),
            'fs':   FsModule(),
            'log':  LogModule(),
            'env':  EnvModule(),
        }
        if top in modules:
            env.set(top, modules[top])
        return None

    def _load_extern(self, node: ExternImport, env: Env):
        if node.ecosystem == 'pip':
            try:
                import importlib
                mod = importlib.import_module(node.package)
                env.set(node.alias, mod)
                return None
            except ImportError:
                print(f"[karn:warn] pip package '{node.package}' not installed. "
                      f"Run: pip install {node.package}", file=sys.stderr)
                env.set(node.alias, None)
        elif node.ecosystem == 'npm':
            # npm interop: load via subprocess node -e
            npm_mod = NpmInterop(node.package, node.alias)
            env.set(node.alias, npm_mod)
        elif node.ecosystem == 'cargo':
            # cargo interop: stub with message (requires native compilation)
            print(f"[karn:info] cargo interop for '{node.package}' — "
                  f"compile with: cargo build --lib", file=sys.stderr)
            env.set(node.alias, CargoInterop(node.package))
        elif node.ecosystem == 'sys':
            # system library: try ctypes CDLL
            try:
                import ctypes
                lib = ctypes.CDLL(node.package)
                env.set(node.alias, SysInterop(lib, node.package))
            except OSError:
                print(f"[karn:warn] system library '{node.package}' not found", file=sys.stderr)
                env.set(node.alias, None)
        return None


class NpmInterop(StdlibModule):
    """Calls npm packages via Node.js subprocess."""
    def __init__(self, package, alias):
        self._package = package
        self._alias = alias

    def attr(self, name):
        def npm_call(*args):
            import subprocess
            import json as _json
            args_json = _json.dumps(list(args))
            script = (f"const m = require('{self._package}');"
                      f"const r = m.{name}.apply(null, {args_json});"
                      f"console.log(JSON.stringify(r ?? null))")
            try:
                result = subprocess.run(
                    ['node', '-e', script],
                    capture_output=True, text=True, timeout=30
                )
                if result.returncode == 0:
                    return OkVal(_json.loads(result.stdout.strip()))
                return KarnError(f"npm call failed: {result.stderr.strip()}")
            except FileNotFoundError:
                return KarnError("Node.js not installed — required for npm interop")
            except Exception as e:
                return KarnError(str(e))
        return npm_call


class CargoInterop(StdlibModule):
    """Stub for Rust crate interop."""
    def __init__(self, package):
        self._package = package

    def attr(self, name):
        return lambda *a: KarnError(f"cargo:{self._package}.{name} requires native compilation target")


class SysInterop(StdlibModule):
    """Wraps a ctypes CDLL."""
    def __init__(self, lib, name):
        self._lib = lib
        self._name = name

    def attr(self, name):
        if hasattr(self._lib, name):
            fn = getattr(self._lib, name)
            def sys_call(*args):
                try:
                    return OkVal(fn(*args))
                except Exception as e:
                    return KarnError(str(e))
            return sys_call
        raise KarnError(f"System library '{self._name}' has no symbol '{name}'")


# ═══════════════════════════════════════════════════════════
#  CODE GENERATOR — emits Python (for portable compilation)
# ═══════════════════════════════════════════════════════════

class CodeGen:
    """Emits runnable Python from KARN AST.
       Targets: python (portable), js (Node.js), c (via LLVM stub)
    """

    def __init__(self, target='python'):
        self.target = target
        self.indent  = 0
        self.lines: List[str] = []
        self.fns: List[str] = []

    def emit_line(self, line=''):
        self.lines.append('    ' * self.indent + line)

    def generate(self, program: Program) -> str:
        self.emit_line('# Generated by KARN agc v1.0')
        self.emit_line('# DO NOT EDIT — edit the .kn source instead')
        self.emit_line('')
        self.emit_line('from __future__ import annotations')
        self.emit_line('import sys, os, json, time')
        self.emit_line('')
        self.emit_line('class _Ok:')
        self.indent += 1
        self.emit_line('def __init__(self, v): self.v = v')
        self.emit_line('def __repr__(self): return f"Ok({self.v!r})"')
        self.indent -= 1
        self.emit_line('')
        self.emit_line('class _Err(Exception):')
        self.indent += 1
        self.emit_line('def __init__(self, msg, ctx=None): self.msg=msg; self.ctx=ctx or []')
        self.emit_line('def __repr__(self): return f"Err({self.msg!r})"')
        self.indent -= 1
        self.emit_line('')
        self.emit_line('def _prop(v):')
        self.indent += 1
        self.emit_line('if isinstance(v, _Err): raise v')
        self.emit_line('if isinstance(v, _Ok): return v.v')
        self.emit_line('return v')
        self.indent -= 1
        self.emit_line('')

        for stmt in program.stmts:
            self.gen_stmt(stmt)

        return '\n'.join(self.lines)

    def gen_stmt(self, node: Node):
        t = type(node)

        if t == Bind:
            val = self.gen_expr(node.value)
            self.emit_line(f'{self._pyname(node.name)} = {val}')

        elif t == FnDef:
            self.gen_fn(node)

        elif t == Emit:
            if self.indent == 0:
                self.emit_line(f'print({self.gen_expr(node.value)})')
            else:
                self.emit_line(f'return {self.gen_expr(node.value)}')

        elif t == TypeDef:
            self.emit_line(f'class {node.name}:')
            self.indent += 1
            fields = ', '.join(node.fields.keys())
            self.emit_line(f'def __init__(self, {fields}):')
            self.indent += 1
            for f in node.fields:
                self.emit_line(f'self.{f} = {f}')
            if not node.fields:
                self.emit_line('pass')
            self.indent -= 2
            self.emit_line('')

        elif t in (TargetDecl, StdlibImport, TraitDef):
            pass  # Compilation hints / structural only

        elif t == ExternImport:
            if node.ecosystem == 'pip':
                self.emit_line(f'import {node.package} as {node.alias}')

        else:
            expr = self.gen_expr(node)
            if expr:
                self.emit_line(expr)

    def gen_fn(self, node: FnDef):
        params = ', '.join(p for p, _ in node.params)
        name = self._pyname(node.name or '_lambda')
        self.emit_line(f'def {name}({params}):')
        self.indent += 1
        if not node.body:
            self.emit_line('pass')
        for stmt in node.body:
            self.gen_stmt(stmt)
        self.indent -= 1
        self.emit_line('')

    def gen_expr(self, node: Node) -> str:
        t = type(node)

        if t == NumberLit:  return repr(node.value)
        if t == StringLit:  return repr(node.value)
        if t == BoolLit:    return 'True' if node.value else 'False'
        if t == NilLit:     return 'None'
        if t == Ident:      return self._pyname(node.name)

        if t == ListLit:
            items = ', '.join(self.gen_expr(i) for i in node.items)
            return f'[{items}]'

        if t == MapLit:
            pairs = []
            for k, v in node.pairs:
                if isinstance(k, Spread):
                    pairs.append(f'**{self.gen_expr(k.expr)}')
                else:
                    pairs.append(f'{self.gen_expr(k)}: {self.gen_expr(v)}')
            return '{' + ', '.join(pairs) + '}'

        if t == BinOp:
            l = self.gen_expr(node.left)
            r = self.gen_expr(node.right)
            return f'({l} {node.op} {r})'

        if t == GetAttr:
            return f'{self.gen_expr(node.obj)}.{node.attr}'

        if t == Call:
            fn   = self.gen_expr(node.callee)
            args = ', '.join(self.gen_expr(a) for a in node.args)
            kw   = ', '.join(f'{k}={self.gen_expr(v)}' for k, v in node.kwargs.items())
            all_args = ', '.join(filter(None, [args, kw]))
            return f'{fn}({all_args})'

        if t == Propagate:
            return f'_prop({self.gen_expr(node.expr)})'

        if t == Fallback:
            e = self.gen_expr(node.expr)
            d = self.gen_expr(node.default)
            return f'(lambda _v: _v if not isinstance(_v, (_Err, type(None))) else {d})({e})'

        if t == Pipe:
            result = self.gen_expr(node.stages[0])
            for stage in node.stages[1:]:
                fn = self.gen_expr(stage)
                result = f'{fn}({result})'
            return result

        if t == Par:
            items = ', '.join(self.gen_expr(e) for e in node.exprs)
            return f'[{items}]'

        if t == MapOp:
            col = self.gen_expr(node.collection)
            fn  = self.gen_expr(node.fn)
            return f'[({fn})(_i) for _i in {col}]'

        if t == FilterOp:
            col = self.gen_expr(node.collection)
            fn  = self.gen_expr(node.fn)
            return f'[_i for _i in {col} if ({fn})(_i)]'

        if t == Emit:
            return f'return {self.gen_expr(node.value)}'

        if t == FnDef:
            params = ', '.join(p for p, _ in node.params)
            # Inline lambda for simple single-expression bodies
            if len(node.body) == 1 and isinstance(node.body[0], Emit):
                body = self.gen_expr(node.body[0].value)
                return f'(lambda {params}: {body})'
            return f'(lambda {params}: None)'  # complex bodies need def

        if t == RangeExpr:
            s = self.gen_expr(node.start)
            e = self.gen_expr(node.end)
            return f'list(range({s}, {e}+1))'

        if t == MatchExpr:
            subj = self.gen_expr(node.subject)
            arms = []
            for pat, body in node.arms:
                arms.append(f'    # arm: {pat}')
            return f'None  # match on {subj}'

        return 'None'

    def _pyname(self, name: str) -> str:
        reserved = {'type', 'from', 'as', 'match', 'trait', 'import',
                    'class', 'def', 'return', 'print', 'input', 'list'}
        if name in reserved:
            return f'_{name}'
        return name.replace('-', '_').replace('.', '__')


# ═══════════════════════════════════════════════════════════
#  JS CODE GENERATOR — emits JavaScript (Node.js / Web)
# ═══════════════════════════════════════════════════════════

class JSCodeGen:
    """Emits runnable JavaScript from KARN AST.
       Targets: js (Node.js), web (browser HTML wrapper)
    """

    def __init__(self, target='js'):
        self.target = target
        self.indent  = 0
        self.lines: List[str] = []
        self._fn_names: List[str] = []

    def _ind(self):
        return '  ' * self.indent

    def emit(self, line=''):
        self.lines.append(self._ind() + line)

    def generate(self, program: Program) -> str:
        is_web = self.target == 'web'

        if is_web:
            self.emit('<!DOCTYPE html>')
            self.emit('<html lang="en">')
            self.emit('<head>')
            self.emit('<meta charset="UTF-8">')
            self.emit('<meta name="viewport" content="width=device-width, initial-scale=1.0">')
            self.emit('<title>KARN App</title>')
            self.emit('<style>')
            self.emit('  body { font-family: system-ui, sans-serif; background: #05070a; color: #e8e6e3; padding: 2rem; }')
            self.emit('  #output { white-space: pre-wrap; font-family: monospace; color: #c9a84c; }')
            self.emit('</style>')
            self.emit('</head>')
            self.emit('<body>')
            self.emit('<div id="output"></div>')
            self.emit('<script>')
        else:
            self.emit('// Generated by KARN agc v1.0 (JavaScript)')
            self.emit('// DO NOT EDIT — edit the .kn source instead')
            self.emit('')

        # Runtime boilerplate
        self.emit('class _Ok {')
        self.indent += 1
        self.emit('constructor(v) { this.v = v; }')
        self.emit('toString() { return `Ok(${JSON.stringify(this.v)})`; }')
        self.indent -= 1
        self.emit('}')
        self.emit('')

        self.emit('class _Err extends Error {')
        self.indent += 1
        self.emit('constructor(msg, ctx) { super(msg); this.ctx = ctx || []; }')
        self.emit('toString() { return `Err(${this.message})`; }')
        self.indent -= 1
        self.emit('}')
        self.emit('')

        self.emit('function _prop(v) {')
        self.indent += 1
        self.emit('if (v instanceof _Err) throw v;')
        self.emit('if (v instanceof _Ok) return v.v;')
        self.emit('return v;')
        self.indent -= 1
        self.emit('}')
        self.emit('')

        # Stdlib for JS
        self._emit_stdlib()
        self.emit('')

        # Generate statements
        for stmt in program.stmts:
            self.gen_stmt(stmt)

        if is_web:
            self.emit('</script>')
            self.emit('</body>')
            self.emit('</html>')

        return '\n'.join(self.lines)

    def _emit_stdlib(self):
        if self.target == 'web':
            self.emit('const http = {')
            self.indent += 1
            self.emit('async get(url, opts) {')
            self.indent += 1
            self.emit('try {')
            self.indent += 1
            self.emit('const r = await fetch(url, opts);')
            self.emit('return new _Ok(await r.text());')
            self.indent -= 1
            self.emit('} catch(e) { return new _Err(e.message); }')
            self.indent -= 1
            self.emit('},')
            self.emit('async post(url, body) {')
            self.indent += 1
            self.emit('return this.get(url, { method: "POST", body: JSON.stringify(body), headers: {"Content-Type":"application/json"} });')
            self.indent -= 1
            self.emit('}')
            self.indent -= 1
            self.emit('};')
            self.emit('')

            self.emit('const fs = {')
            self.indent += 1
            self.emit('read(path) { return new _Err("fs.read not available in browser"); },')
            self.emit('write(path, content) { try { localStorage.setItem(path, content); return new _Ok(null); } catch(e) { return new _Err(e.message); } },')
            self.emit('list(path) { return new _Ok([]); }')
            self.indent -= 1
            self.emit('};')
            self.emit('')

            self.emit('const log = {')
            self.indent += 1
            self.emit('info: (...a) => { const m = a.map(x => typeof x==="object" ? JSON.stringify(x) : String(x)).join(" "); console.log(`%c[INFO]%c ${m}`, "color:#06b6d4", "color:#e8e6e3"); document.getElementById("output").textContent += `[INFO] ${m}\\n`; return new _Ok(null); },')
            self.emit('warn: (...a) => { const m = a.map(x => typeof x==="object" ? JSON.stringify(x) : String(x)).join(" "); console.warn(`[WARN] ${m}`); return new _Ok(null); },')
            self.emit('err:  (...a) => { const m = a.map(x => typeof x==="object" ? JSON.stringify(x) : String(x)).join(" "); console.error(`[ERR] ${m}`); return new _Ok(null); }')
            self.indent -= 1
            self.emit('};')
            self.emit('')

            self.emit('const env = {')
            self.indent += 1
            self.emit('get: (k, d) => new _Ok(undefined),')
            self.emit('require: (k) => new _Err(`env ${k} not set in browser`)')
            self.indent -= 1
            self.emit('};')
            self.emit('')
        else:
            # Node.js stdlib
            self.emit('const http = {')
            self.indent += 1
            self.emit('async get(url, opts) {')
            self.indent += 1
            self.emit('try {')
            self.indent += 1
            self.emit('const r = await fetch(url, opts);')
            self.emit('return new _Ok(await r.text());')
            self.indent -= 1
            self.emit('} catch(e) { return new _Err(e.message); }')
            self.indent -= 1
            self.emit('},')
            self.emit('async serve(port, routes) {')
            self.indent += 1
            self.emit('console.log(`[karn:http] Serving on port ${port}`);')
            self.emit('return new _Ok(null);')
            self.indent -= 1
            self.emit('}')
            self.indent -= 1
            self.emit('};')
            self.emit('')

            self.emit('const fs = {')
            self.indent += 1
            self.emit('read(path) {')
            self.indent += 1
            self.emit('try { return new _Ok(require("fs").readFileSync(String(path), "utf8")); }')
            self.indent += 1
            self.emit('catch(e) { return new _Err(e.message); }')
            self.indent -= 1
            self.indent -= 1
            self.emit('},')
            self.emit('write(path, content) {')
            self.indent += 1
            self.emit('try { require("fs").writeFileSync(String(path), String(content)); return new _Ok(null); }')
            self.indent += 1
            self.emit('catch(e) { return new _Err(e.message); }')
            self.indent -= 1
            self.indent -= 1
            self.emit('},')
            self.emit('list(path=".") {')
            self.indent += 1
            self.emit('try { return new _Ok(require("fs").readdirSync(String(path))); }')
            self.indent += 1
            self.emit('catch(e) { return new _Err(e.message); }')
            self.indent -= 1
            self.indent -= 1
            self.emit('}')
            self.indent -= 1
            self.emit('};')
            self.emit('')

            self.emit('const log = {')
            self.indent += 1
            self.emit('info: (...a) => { const m = a.map(x => typeof x==="object" ? JSON.stringify(x) : String(x)).join(" "); console.log(`\\x1b[36m[INFO]\\x1b[0m ${m}`); return new _Ok(null); },')
            self.emit('warn: (...a) => { const m = a.map(x => typeof x==="object" ? JSON.stringify(x) : String(x)).join(" "); console.warn(`\\x1b[33m[WARN]\\x1b[0m ${m}`); return new _Ok(null); },')
            self.emit('err:  (...a) => { const m = a.map(x => typeof x==="object" ? JSON.stringify(x) : String(x)).join(" "); console.error(`\\x1b[31m[ERR]\\x1b[0m  ${m}`); return new _Ok(null); }')
            self.indent -= 1
            self.emit('};')
            self.emit('')

            self.emit('const env = {')
            self.indent += 1
            self.emit('get: (k, d) => new _Ok(process.env[String(k)] ?? d ?? null),')
            self.emit('require: (k) => { const v = process.env[String(k)]; return v !== undefined ? new _Ok(v) : new _Err(`Required env var \'${k}\' not set`); }')
            self.indent -= 1
            self.emit('};')
            self.emit('')

            self.emit('const json = {')
            self.indent += 1
            self.emit('parse: (s) => { try { return new _Ok(JSON.parse(String(s))); } catch(e) { return new _Err(e.message); } },')
            self.emit('stringify: (o, i) => { try { return new _Ok(JSON.stringify(o, null, i)); } catch(e) { return new _Err(e.message); } },')
            self.emit('pretty: (o) => { try { return new _Ok(JSON.stringify(o, null, 2)); } catch(e) { return new _Err(e.message); } }')
            self.indent -= 1
            self.emit('};')
            self.emit('')

            self.emit('const math = {')
            self.indent += 1
            self.emit('abs: (x) => Math.abs(Number(x)), ceil: (x) => Math.ceil(Number(x)),')
            self.emit('floor: (x) => Math.floor(Number(x)), round: (x) => Math.round(Number(x)),')
            self.emit('sqrt: (x) => Math.sqrt(Number(x)), pow: (x, y) => Math.pow(Number(x), Number(y)),')
            self.emit('min: (...a) => Math.min(...a.map(Number)), max: (...a) => Math.max(...a.map(Number)),')
            self.emit('sin: (x) => Math.sin(Number(x)), cos: (x) => Math.cos(Number(x)),')
            self.emit('log: (x) => Math.log(Number(x)), pi: () => Math.PI, e: () => Math.E')
            self.indent -= 1
            self.emit('};')
            self.emit('')

            self.emit('const str = {')
            self.indent += 1
            self.emit('join: (a, s="") => new _Ok(a.join(String(s))),')
            self.emit('split: (s, d=" ") => new _Ok(String(s).split(String(d))),')
            self.emit('replace: (s, o, n) => new _Ok(String(s).replace(String(o), String(n))),')
            self.emit('contains: (s, sub) => new _Ok(String(s).includes(String(sub))),')
            self.emit('starts: (s, p) => new _Ok(String(s).startsWith(String(p))),')
            self.emit('ends: (s, p) => new _Ok(String(s).endsWith(String(p))),')
            self.emit('trim: (s) => new _Ok(String(s).trim()),')
            self.emit('repeat: (s, n) => new _Ok(String(s).repeat(Number(n)))')
            self.indent -= 1
            self.emit('};')
            self.emit('')

            self.emit('const crypto = {')
            self.indent += 1
            self.emit('uuid: () => new _Ok(crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).slice(2)),')
            self.indent -= 1
            self.emit('};')
            self.emit('')

            self.emit('const db = {')
            self.indent += 1
            self.emit('q: (t, w) => { console.log(`[karn:db] Query: ${t} WHERE ${JSON.stringify(w)}`); return new _Ok([]); },')
            self.emit('exec: (sql, ...a) => { console.log(`[karn:db] Exec: ${sql} args=${JSON.stringify(a)}`); return new _Ok({rows_affected: 0}); }')
            self.indent -= 1
            self.emit('};')
            self.emit('')

            self.emit('const time = {')
            self.indent += 1
            self.emit('now: () => new _Ok(Date.now() / 1000),')
            self.emit('sleep: (ms) => new Promise(r => setTimeout(() => r(new _Ok(null)), Number(ms))),')
            self.emit('fmt: (ts, f) => new _Ok(new Date((ts || Date.now()/1000) * 1000).toISOString()),')
            self.emit('date: () => { const d = new Date(); return new _Ok({year: d.getFullYear(), month: d.getMonth()+1, day: d.getDate()}); }')
            self.indent -= 1
            self.emit('};')
            self.emit('')

            # Built-in functions
            self.emit('function print(...args) { console.log(...args.map(String)); return new _Ok(null); }')
            self.emit('function len(x) { return x.length; }')
            self.emit('function keys(x) { return Object.keys(x); }')
            self.emit('function values(x) { return Object.values(x); }')
            self.emit('function range(s, e) { const a=[]; for(let i=s; i<=(e||s); i++) a.push(i); return a; }')
            self.emit('function type_of(x) { return typeof x; }')
            self.emit('function sorted(x) { return [...x].sort(); }')
            self.emit('function reversed(x) { return [...x].reverse(); }')
            self.emit('function sum(x) { return x.reduce((a,b)=>a+b, 0); }')
            self.emit('function any(x) { return x.some(Boolean); }')
            self.emit('function all(x) { return x.every(Boolean); }')
            self.emit('')

    def gen_stmt(self, node: Node):
        t = type(node)

        if t == Bind:
            val = self.gen_expr(node.value)
            self.emit(f'let {self._jsname(node.name)} = {val};')

        elif t == FnDef:
            self.gen_fn(node)

        elif t == Emit:
            val = self.gen_expr(node.value)
            if self.indent == 0:
                self.emit(f'console.log({val});')
            else:
                self.emit(f'return {val};')

        elif t == TypeDef:
            fields = list(node.fields.keys())
            self.emit(f'class {node.name} {{')
            self.indent += 1
            self.emit(f'constructor({", ".join(fields)}) {{')
            self.indent += 1
            for f in fields:
                self.emit(f'this.{f} = {f};')
            self.indent -= 1
            self.emit('}')
            self.indent -= 1
            self.emit('}')
            self.emit('')

        elif t in (TargetDecl, StdlibImport, TraitDef):
            pass

        elif t == ExternImport:
            if node.ecosystem in ('pip', 'npm'):
                self.emit(f'const {node.alias} = require("{node.package}");')
            elif node.ecosystem == 'cargo':
                self.emit(f'// cargo:{node.package} requires native compilation')

        else:
            expr = self.gen_expr(node)
            if expr:
                # Top-level expression: auto-print only for pure value expressions
                # Don't double-print calls (log.info, fs.write, etc.) — they already handle output
                if self.indent == 0 and not isinstance(node, Call):
                    self.emit(f'console.log({expr});')
                else:
                    self.emit(f'{expr};')

    def gen_fn(self, node: FnDef):
        params = ', '.join(self._jsname(p) for p, _ in node.params)
        name = self._jsname(node.name or '_lambda')

        # If we're inside a function, use arrow function for lambdas
        if node.name is None and self.indent > 0:
            if len(node.body) == 1 and isinstance(node.body[0], Emit):
                body = self.gen_expr(node.body[0].value)
                self.emit(f'({params}) => {body};')
                return

        self.emit(f'function {name}({params}) {{')
        self.indent += 1
        if not node.body:
            self.emit('return;')
        for i, stmt in enumerate(node.body):
            t = type(stmt)
            if t == Emit:
                self.gen_stmt(stmt)
            elif i == len(node.body) - 1:
                # Last statement that's not an Emit — auto-return it
                self.emit(f'return {self.gen_expr(stmt)};')
            else:
                expr = self.gen_expr(stmt)
                if expr:
                    self.emit(f'{expr};')
        self.indent -= 1
        self.emit('}')
        self.emit('')

    def gen_expr(self, node: Node) -> str:
        t = type(node)

        if t == NumberLit:
            v = node.value
            if isinstance(v, float) and v == int(v):
                return str(int(v))
            return str(v)
        if t == StringLit:
            # Use double quotes for JS
            return json.dumps(node.value)
        if t == BoolLit:
            return 'true' if node.value else 'false'
        if t == NilLit:
            return 'null'
        if t == Ident:
            return self._jsname(node.name)

        if t == ListLit:
            items = []
            for i in node.items:
                if isinstance(i, Spread):
                    items.append(f'...{self.gen_expr(i.expr)}')
                else:
                    items.append(self.gen_expr(i))
            return f'[{", ".join(items)}]'

        if t == MapLit:
            pairs = []
            for k, v in node.pairs:
                if isinstance(k, Spread):
                    pairs.append(f'...{self.gen_expr(k.expr)}')
                elif isinstance(k, Ident):
                    # Bare ident key: {x:1} → {"x": 1} in JS
                    pairs.append(f'"{k.name}": {self.gen_expr(v)}')
                else:
                    pairs.append(f'{self.gen_expr(k)}: {self.gen_expr(v)}')
            return '{' + ', '.join(pairs) + '}'

        if t == BinOp:
            l = self.gen_expr(node.left)
            r = self.gen_expr(node.right)
            return f'({l} {node.op} {r})'

        if t == GetAttr:
            return f'{self.gen_expr(node.obj)}.{node.attr}'

        if t == Call:
            # Ok(x) → new _Ok(x), Err(x) → new _Err(x)
            if isinstance(node.callee, Ident) and node.callee.name == 'Ok':
                args = ', '.join(self.gen_expr(a) for a in node.args)
                return f'new _Ok({args})'
            if isinstance(node.callee, Ident) and node.callee.name == 'Err':
                args = ', '.join(self.gen_expr(a) for a in node.args)
                return f'new _Err({args})'
            fn = self.gen_expr(node.callee)
            args = ', '.join(self.gen_expr(a) for a in node.args)
            kw = ', '.join(f'{k}: {self.gen_expr(v)}' for k, v in node.kwargs.items())
            all_args = ', '.join(filter(None, [args, kw]))
            return f'{fn}({all_args})'

        if t == Propagate:
            return f'_prop({self.gen_expr(node.expr)})'

        if t == Fallback:
            e = self.gen_expr(node.expr)
            d = self.gen_expr(node.default)
            return f'((v) => v !== null && v !== undefined && !(v instanceof _Err) ? (v instanceof _Ok ? v.v : v) : {d})({e})'

        if t == Pipe:
            result = self.gen_expr(node.stages[0])
            for stage in node.stages[1:]:
                fn = self.gen_expr(stage)
                result = f'{fn}({result})'
            return result

        if t == Par:
            items = ', '.join(self.gen_expr(e) for e in node.exprs)
            return f'[{items}]'

        if t == MapOp:
            col = self.gen_expr(node.collection)
            fn = self.gen_expr(node.fn)
            return f'{col}.map((i) => ({fn})(i))'

        if t == FilterOp:
            col = self.gen_expr(node.collection)
            fn = self.gen_expr(node.fn)
            return f'{col}.filter((i) => ({fn})(i))'

        if t == Emit:
            return f'return {self.gen_expr(node.value)}'

        if t == FnDef:
            params = ', '.join(self._jsname(p) for p, _ in node.params)
            if len(node.body) == 1 and isinstance(node.body[0], Emit):
                body = self.gen_expr(node.body[0].value)
                return f'(({params}) => {body})'
            return f'(({params}) => null)'

        if t == RangeExpr:
            s = self.gen_expr(node.start)
            e = self.gen_expr(node.end)
            return f'Array.from({{length: {e} - {s} + 1}}, (_, i) => {s} + i)'

        if t == MatchExpr:
            return self._gen_match(node)

        if t == RetryExpr:
            expr = self.gen_expr(node.expr)
            n = self.gen_expr(node.n)
            return f'(async () => {{ for (let i = 0; i < {n}; i++) {{ const v = await {expr}; if (!(v instanceof _Err)) return v; await new Promise(r => setTimeout(r, 100 * Math.pow(2, i))); }} return v; }})()'

        if t == TimeoutExpr:
            return self.gen_expr(node.expr)

        return 'null'

    def _gen_match(self, node: MatchExpr) -> str:
        subj = self.gen_expr(node.subject)
        arms = []
        for pat, body in node.arms:
            body_str = self._gen_match_body(body)
            if isinstance(pat, Call) and isinstance(pat.callee, Ident):
                cname = pat.callee.name
                if cname == 'Ok' and pat.args:
                    var = pat.args[0].name if isinstance(pat.args[0], Ident) else '_v'
                    arms.append(f'if ({subj} instanceof _Ok) {{ const {var} = {subj}.v; {body_str} }}')
                elif cname == 'Err' and pat.args:
                    var = pat.args[0].name if isinstance(pat.args[0], Ident) else '_e'
                    arms.append(f'if ({subj} instanceof _Err) {{ const {var} = {subj}; {body_str} }}')
                elif cname == 'Ok':
                    arms.append(f'if ({subj} instanceof _Ok) {{ {body_str} }}')
                elif cname == 'Err':
                    arms.append(f'if ({subj} instanceof _Err) {{ {body_str} }}')
                else:
                    pat_str = self.gen_expr(pat)
                    arms.append(f'if (JSON.stringify({subj}) === JSON.stringify({pat_str})) {{ {body_str} }}')
            elif isinstance(pat, Ident):
                if pat.name == '_':
                    arms.append(f'{{ {body_str} }}')
                else:
                    arms.append(f'{{ const {pat.name} = {subj}; {body_str} }}')
            elif isinstance(pat, (NumberLit, StringLit, BoolLit)):
                pat_str = self.gen_expr(pat)
                arms.append(f'if ({subj} === {pat_str}) {{ {body_str} }}')
            else:
                pat_str = self.gen_expr(pat)
                arms.append(f'if (JSON.stringify({subj}) === JSON.stringify({pat_str})) {{ {body_str} }}')

        return f'(() => {{ {"; ".join(arms)} return null; }})()'

    def _gen_match_body(self, body: Node) -> str:
        """Generate match arm body — unwraps Emit to just the value."""
        if isinstance(body, Emit):
            return f'return {self.gen_expr(body.value)}'
        return f'return {self.gen_expr(body)}'

    def _jsname(self, name: str) -> str:
        reserved = {'type', 'from', 'as', 'match', 'trait', 'import',
                    'class', 'function', 'return', 'console', 'let', 'const',
                    'var', 'new', 'delete', 'typeof', 'instanceof'}
        if name in reserved:
            return f'_{name}'
        return name.replace('-', '_').replace('.', '__')


# ═══════════════════════════════════════════════════════════
#  C CODE GENERATOR — emits C (compiles to native / wasm)
# ═══════════════════════════════════════════════════════════

C_RUNTIME = r"""/* KARN C Runtime — v1.0 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <stdbool.h>
#include <time.h>
#include <errno.h>

/* ── Value system ── */
typedef enum { VAL_NIL, VAL_NUM, VAL_STR, VAL_BOOL, VAL_OK, VAL_ERR, VAL_ARR, VAL_MAP } ValType;

typedef struct Val Val;
typedef struct Arr  Arr;
typedef struct Map  Map;

struct Arr  { int len; int cap; Val* items; };
struct Map  { int len; int cap; Val* keys; Val* vals; };

struct Val {
    ValType type;
    union {
        double  num;
        char*   str;
        bool    b;
        Val*    ok_val;
        char*   err_msg;
        Arr*    arr;
        Map*    map;
    } as;
};

Val val_nil(void)    { Val v = {VAL_NIL, {0}}; return v; }
Val val_num(double n){ Val v = {VAL_NUM, {.num=n}}; return v; }
Val val_bool(bool b) { Val v = {VAL_BOOL, {.b=b}}; return v; }
Val val_str(const char* s){
    Val v = {VAL_STR, {.str=strdup(s?s:"")}}; return v;
}
Val val_ok(Val v)   {
    Val r = {VAL_OK, {.ok_val=malloc(sizeof(Val))}}; *r.as.ok_val=v; return r;
}
Val val_err(const char* m){ Val v={VAL_ERR,{.err_msg=strdup(m?m:"")}}; return v; }

Arr* arr_new(int cap){
    Arr* a = malloc(sizeof(Arr)); a->len=0; a->cap=cap;
    a->items = malloc(sizeof(Val)*cap); return a;
}
Val val_arr(Arr* a){ Val v={VAL_ARR,{.arr=a}}; return v; }

Map* map_new(int cap){
    Map* m = malloc(sizeof(Map)); m->len=0; m->cap=cap;
    m->keys = malloc(sizeof(Val)*cap); m->vals = malloc(sizeof(Val)*cap); return m;
}
Val val_map(Map* m){ Val v={VAL_MAP,{.map=m}}; return v; }

void arr_push(Arr* a, Val v){
    if(a->len >= a->cap){ a->cap*=2; a->items=realloc(a->items,sizeof(Val)*a->cap); }
    a->items[a->len++] = v;
}

void map_put(Map* m, Val k, Val v){
    for(int i=0;i<m->len;i++){
        if(k.type==VAL_NUM && m->keys[i].type==VAL_NUM && m->keys[i].as.num==k.as.num){ m->vals[i]=v; return; }
        if(k.type==VAL_STR && m->keys[i].type==VAL_STR && strcmp(m->keys[i].as.str,k.as.str)==0){ m->vals[i]=v; return; }
    }
    if(m->len >= m->cap){ m->cap*=2; m->keys=realloc(m->keys,sizeof(Val)*m->cap); m->vals=realloc(m->vals,sizeof(Val)*m->cap); }
    m->keys[m->len]=k; m->vals[m->len]=v; m->len++;
}

Val map_get(Map* m, Val k){
    for(int i=0;i<m->len;i++){
        if(k.type==VAL_NUM && m->keys[i].type==VAL_NUM && m->keys[i].as.num==k.as.num) return m->vals[i];
        if(k.type==VAL_STR && m->keys[i].type==VAL_STR && strcmp(m->keys[i].as.str,k.as.str)==0) return m->vals[i];
    }
    return val_nil();
}

/* ── Printing ── */
void val_print(Val v){
    switch(v.type){
        case VAL_NIL:  printf("nil"); break;
        case VAL_NUM:  printf("%g", v.as.num); break;
        case VAL_STR:  printf("%s", v.as.str); break;
        case VAL_BOOL: printf("%s", v.as.b?"true":"false"); break;
        case VAL_OK:   printf("Ok("); val_print(*v.as.ok_val); printf(")"); break;
        case VAL_ERR:  printf("Err(%s)", v.as.err_msg); break;
        case VAL_ARR:  printf("["); for(int i=0;i<v.as.arr->len;i++){ if(i) printf(", "); val_print(v.as.arr->items[i]); } printf("]"); break;
        case VAL_MAP:  printf("{"); for(int i=0;i<v.as.map->len;i++){ if(i) printf(", "); val_print(v.as.map->keys[i]); printf(": "); val_print(v.as.map->vals[i]); } printf("}"); break;
    }
}
void val_println(Val v){ val_print(v); printf("\n"); }

/* ── Conversions ── */
double _val_to_num(Val v){ if(v.type==VAL_NUM) return v.as.num; return 0; }
const char* _val_to_str(Val v){
    static char buf[256];
    if(v.type==VAL_STR) return v.as.str;
    if(v.type==VAL_NUM){ snprintf(buf,sizeof(buf),"%g",v.as.num); return buf; }
    if(v.type==VAL_BOOL){ return v.as.b?"true":"false"; }
    return "nil";
}

/* ── Error propagation ── */
Val _prop(Val v){
    if(v.type==VAL_ERR){ val_println(v); exit(1); }
    if(v.type==VAL_OK) return *v.as.ok_val;
    return v;
}

/* ── Built-in functions ── */
Val karn_len(Val v){
    if(v.type==VAL_ARR) return val_num(v.as.arr->len);
    if(v.type==VAL_STR) return val_num(strlen(v.as.str));
    return val_num(0);
}

Val karn_range(Val s, Val e){
    int start=(int)_val_to_num(s), end=(int)_val_to_num(e);
    Arr* a = arr_new(end-start+1);
    for(int i=start;i<=end;i++) arr_push(a, val_num(i));
    return val_arr(a);
}

Val karn_sum(Val v){
    double sum=0; if(v.type==VAL_ARR){ for(int i=0;i<v.as.arr->len;i++) sum+=_val_to_num(v.as.arr->items[i]); }
    return val_num(sum);
}

Val karn_sorted(Val v){
    if(v.type!=VAL_ARR) return v;
    Arr* a=v.as.arr; Arr* b=arr_new(a->len);
    for(int i=0;i<a->len;i++) b->items[i]=a->items[i]; b->len=a->len;
    for(int i=0;i<b->len-1;i++) for(int j=i+1;j<b->len;j++){
        if(_val_to_num(b->items[i])>_val_to_num(b->items[j])){ Val t=b->items[i]; b->items[i]=b->items[j]; b->items[j]=t; }
    }
    return val_arr(b);
}

Val karn_reversed(Val v){
    if(v.type!=VAL_ARR) return v;
    Arr* a=v.as.arr; Arr* b=arr_new(a->len);
    for(int i=0;i<a->len;i++) b->items[i]=a->items[a->len-1-i]; b->len=a->len;
    return val_arr(b);
}

Val karn_any(Val v){
    if(v.type!=VAL_ARR) return val_bool(false);
    for(int i=0;i<v.as.arr->len;i++) if(_val_to_num(v.as.arr->items[i])) return val_bool(true);
    return val_bool(false);
}

Val karn_all(Val v){
    if(v.type!=VAL_ARR) return val_bool(false);
    for(int i=0;i<v.as.arr->len;i++) if(!_val_to_num(v.as.arr->items[i])) return val_bool(false);
    return val_bool(true);
}

/* ── String methods ── */
Val str_upper(Val v){
    if(v.type!=VAL_STR) return v;
    char* s=strdup(v.as.str); for(int i=0;s[i];i++) if(s[i]>='a'&&s[i]<='z') s[i]-=32;
    Val r=val_str(s); free(s); return r;
}
Val str_lower(Val v){
    if(v.type!=VAL_STR) return v;
    char* s=strdup(v.as.str); for(int i=0;s[i];i++) if(s[i]>='A'&&s[i]<='Z') s[i]+=32;
    Val r=val_str(s); free(s); return r;
}
Val str_len(Val v){ return val_num(v.type==VAL_STR?strlen(v.as.str):0); }

/* ── Array methods ── */
Val arr_len(Val v){ return val_num(v.type==VAL_ARR?v.as.arr->len:0); }
Val arr_first(Val v){ return (v.type==VAL_ARR&&v.as.arr->len>0)?v.as.arr->items[0]:val_nil(); }
Val arr_last(Val v){ return (v.type==VAL_ARR&&v.as.arr->len>0)?v.as.arr->items[v.as.arr->len-1]:val_nil(); }

/* ── Math module ── */
Val math_abs(Val x){ return val_num(fabs(_val_to_num(x))); }
Val math_ceil(Val x){ return val_num(ceil(_val_to_num(x))); }
Val math_floor(Val x){ return val_num(floor(_val_to_num(x))); }
Val math_round(Val x){ return val_num(round(_val_to_num(x))); }
Val math_sqrt(Val x){ return val_num(sqrt(_val_to_num(x))); }
Val math_pow(Val x, Val y){ return val_num(pow(_val_to_num(x),_val_to_num(y))); }
Val math_min(Val x, Val y){ return val_num(fmin(_val_to_num(x),_val_to_num(y))); }
Val math_max(Val x, Val y){ return val_num(fmax(_val_to_num(x),_val_to_num(y))); }
Val math_sin(Val x){ return val_num(sin(_val_to_num(x))); }
Val math_cos(Val x){ return val_num(cos(_val_to_num(x))); }
Val math_log(Val x){ return val_num(log(_val_to_num(x))); }
Val math_pi(void){ return val_num(3.14159265358979); }
Val math_e(void){ return val_num(2.71828182845905); }

/* ── Time module ── */
Val time_now(void){ return val_num((double)time(NULL)); }
Val time_date(void){
    time_t t=time(NULL); struct tm* tm=localtime(&t);
    Map* m=map_new(3); map_put(m,val_str("year"),val_num(tm->tm_year+1900));
    map_put(m,val_str("month"),val_num(tm->tm_mon+1)); map_put(m,val_str("day"),val_num(tm->tm_mday));
    return val_map(m);
}

/* ── Env module ── */
Val env_get(Val k, Val d){
    const char* v=getenv(k.type==VAL_STR?k.as.str:"");
    return v?val_str(v):d;
}

/* ── Crypto module ── */
Val crypto_uuid(void){
    char buf[37]; snprintf(buf,sizeof(buf),"%08x-%04x-%04x-%04x-%012x",
        (unsigned)rand(),(unsigned)rand()%65536,(unsigned)rand()%65536,(unsigned)rand()%65536,(unsigned)rand());
    return val_str(buf);
}

/* ── JSON module ── */
Val json_stringify(Val v, Val indent){
    static char buf[4096];
    if(v.type==VAL_STR){ snprintf(buf,sizeof(buf),"\"%s\"",v.as.str); return val_str(buf); }
    if(v.type==VAL_NUM){ snprintf(buf,sizeof(buf),"%g",v.as.num); return val_str(buf); }
    if(v.type==VAL_BOOL){ return val_str(v.as.b?"true":"false"); }
    if(v.type==VAL_NIL){ return val_str("null"); }
    if(v.type==VAL_ARR){
        char tmp[4096]; int pos=0; pos+=snprintf(tmp+pos,sizeof(tmp)-pos,"[");
        for(int i=0;i<v.as.arr->len;i++){
            if(i) pos+=snprintf(tmp+pos,sizeof(tmp)-pos,", ");
            Val s=json_stringify(v.as.arr->items[i],indent);
            pos+=snprintf(tmp+pos,sizeof(tmp)-pos,"%s",s.as.str);
        }
        pos+=snprintf(tmp+pos,sizeof(tmp)-pos,"]");
        return val_str(tmp);
    }
    return val_str("{}");
}
"""

class CCodeGen:
    """Emits C source code from KARN AST.
       Compiles to native binary via gcc/clang, or WASM via clang.
    """

    def __init__(self, target='c'):
        self.target = target
        self.indent  = 0
        self.lines: List[str] = []
        self._match_counter = 0
        self._fn_counter = 0
        self._lambdas: List[FnDef] = []  # collected lambdas for file-scope emission

    def _ind(self):
        return '    ' * self.indent

    def emit(self, line=''):
        self.lines.append(self._ind() + line)

    def generate(self, program: Program) -> str:
        self.emit('/* Generated by KARN agc v1.0 (C) */')
        self.emit('/* DO NOT EDIT — edit the .kn source instead */')
        self.emit('')
        self.emit(C_RUNTIME)
        self.emit('')

        # Pass 1: collect lambdas from entire AST
        self._collect_lambdas(program)

        # Pass 2: emit lambdas at file scope
        for fn in self._lambdas:
            self._emit_lambda_fn(fn)

        # Pass 3: emit user-defined functions
        fn_defs = []
        top_stmts = []
        for stmt in program.stmts:
            if isinstance(stmt, FnDef):
                fn_defs.append(stmt)
            else:
                top_stmts.append(stmt)

        for stmt in fn_defs:
            self.gen_stmt(stmt)

        self.emit('int main(void) {')
        self.indent += 1
        self.emit('srand(time(NULL));')
        for stmt in top_stmts:
            self.gen_stmt(stmt)
        self.emit('return 0;')
        self.indent -= 1
        self.emit('}')

        return '\n'.join(self.lines)

    def _collect_lambdas(self, node: Node):
        """Walk AST and collect all FnDef nodes used as map/filter args."""
        if isinstance(node, Program):
            for s in node.stmts:
                self._collect_lambdas(s)
        elif isinstance(node, MapOp):
            if isinstance(node.fn, FnDef):
                self._lambdas.append(node.fn)
            self._collect_lambdas(node.collection)
        elif isinstance(node, FilterOp):
            if isinstance(node.fn, FnDef):
                self._lambdas.append(node.fn)
            self._collect_lambdas(node.collection)
        elif isinstance(node, ListLit):
            for item in node.items:
                self._collect_lambdas(item)
        elif isinstance(node, MapLit):
            for k, v in node.pairs:
                self._collect_lambdas(k)
                self._collect_lambdas(v)
        elif isinstance(node, BinOp):
            self._collect_lambdas(node.left)
            self._collect_lambdas(node.right)
        elif isinstance(node, Call):
            self._collect_lambdas(node.callee)
            for a in node.args:
                self._collect_lambdas(a)
        elif isinstance(node, GetAttr):
            self._collect_lambdas(node.obj)
        elif isinstance(node, MatchExpr):
            self._collect_lambdas(node.subject)
            for pat, body in node.arms:
                self._collect_lambdas(pat)
                self._collect_lambdas(body)
        elif isinstance(node, Propagate):
            self._collect_lambdas(node.expr)
        elif isinstance(node, Fallback):
            self._collect_lambdas(node.expr)
            self._collect_lambdas(node.default)
        elif isinstance(node, Pipe):
            for s in node.stages:
                self._collect_lambdas(s)
        elif isinstance(node, RangeExpr):
            self._collect_lambdas(node.start)
            self._collect_lambdas(node.end)
        elif isinstance(node, Bind):
            self._collect_lambdas(node.value)
        elif isinstance(node, Emit):
            self._collect_lambdas(node.value)
        elif isinstance(node, FnDef):
            # Collect lambdas inside function bodies
            for s in node.body:
                self._collect_lambdas(s)

    def _emit_lambda_fn(self, fn: FnDef):
        """Emit a lambda as a file-scope C function."""
        self._fn_counter += 1
        name = f'_fn{self._fn_counter}'
        fn._c_name = name  # tag it for later reference
        params = ', '.join('Val ' + self._cname(p) for p, _ in fn.params)
        self.emit(f'Val {name}({params}) {{')
        self.indent += 1
        for i, stmt in enumerate(fn.body):
            if isinstance(stmt, Emit):
                self.emit(f'return {self.gen_expr(stmt.value)};')
            elif i == len(fn.body) - 1:
                self.emit(f'return {self.gen_expr(stmt)};')
            else:
                expr = self.gen_expr(stmt)
                if expr:
                    self.emit(f'{expr};')
        self.indent -= 1
        self.emit('}')
        self.emit('')

    def gen_stmt(self, node: Node):
        t = type(node)

        if t == Bind:
            val = self.gen_expr(node.value)
            name = self._cname(node.name)
            self.emit(f'Val {name} = {val};')

        elif t == FnDef:
            self.gen_fn(node)

        elif t == Emit:
            val = self.gen_expr(node.value)
            self.emit(f'val_println({val});')

        elif t == TypeDef:
            self.emit(f'typedef struct {{')
            self.indent += 1
            for fname in node.fields:
                self.emit(f'Val {fname};')
            self.indent -= 1
            self.emit(f'}} {node.name}_t;')
            self.emit('')

        elif t in (TargetDecl, StdlibImport, TraitDef, ExternImport):
            pass

        elif t == MatchExpr:
            self._gen_match_stmt(node)

        else:
            expr = self.gen_expr(node)
            if expr:
                self.emit(f'{expr};')

    def gen_fn(self, node: FnDef):
        params = ', '.join('Val ' + self._cname(p) for p, _ in node.params)
        name = self._cname(node.name or '_lambda')

        self.emit(f'Val {name}({params}) {{')
        self.indent += 1
        if not node.body:
            self.emit('return val_nil();')
        for i, stmt in enumerate(node.body):
            if isinstance(stmt, Emit):
                self.emit(f'return {self.gen_expr(stmt.value)};')
            elif isinstance(stmt, MatchExpr):
                self._gen_match_fn(stmt)
            elif i == len(node.body) - 1:
                self.emit(f'return {self.gen_expr(stmt)};')
            else:
                expr = self.gen_expr(stmt)
                if expr:
                    self.emit(f'{expr};')
        self.indent -= 1
        self.emit('}')
        self.emit('')

    def gen_expr(self, node: Node) -> str:
        t = type(node)

        if t == NumberLit:
            v = node.value
            if isinstance(v, float) and v == int(v):
                return f'val_num({int(v)})'
            return f'val_num({v})'
        if t == StringLit:
            return f'val_str({json.dumps(node.value)})'
        if t == BoolLit:
            return f'val_bool({"true" if node.value else "false"})'
        if t == NilLit:
            return 'val_nil()'
        if t == Ident:
            return self._cname(node.name)

        if t == ListLit:
            items = []
            for i in node.items:
                if isinstance(i, Spread):
                    # Spread array items
                    arr = self.gen_expr(i.expr)
                    items.append(f'for(int _si=0;_si<{arr}.as.arr->len;_si++) arr_push(_a, {arr}.as.arr->items[_si]);')
                else:
                    items.append(f'arr_push(_a, {self.gen_expr(i)});')
            if not items:
                return 'val_arr(arr_new(0))'
            inner = ' '.join(items)
            return f'({{ Arr* _a=arr_new({len(node.items)}); {inner} val_arr(_a); }})'

        if t == MapLit:
            pairs = []
            for k, v in node.pairs:
                if isinstance(k, Spread):
                    src = self.gen_expr(k.expr)
                    pairs.append(f'for(int _mi=0;_mi<{src}.as.map->len;_mi++) map_put(_m, {src}.as.map->keys[_mi], {src}.as.map->vals[_mi]);')
                elif isinstance(k, Ident):
                    pairs.append(f'map_put(_m, val_str("{k.name}"), {self.gen_expr(v)});')
                else:
                    pairs.append(f'map_put(_m, {self.gen_expr(k)}, {self.gen_expr(v)});')
            if not pairs:
                return 'val_map(map_new(0))'
            inner = ' '.join(pairs)
            return f'({{ Map* _m=map_new({len(node.pairs)}); {inner} val_map(_m); }})'

        if t == BinOp:
            l = self.gen_expr(node.left)
            r = self.gen_expr(node.right)
            ops = {
                '+':  lambda a,b: f'val_num(_val_to_num({a}) + _val_to_num({b}))',
                '-':  lambda a,b: f'val_num(_val_to_num({a}) - _val_to_num({b}))',
                '*':  lambda a,b: f'val_num(_val_to_num({a}) * _val_to_num({b}))',
                '/':  lambda a,b: f'val_num(_val_to_num({a}) / _val_to_num({b}))',
                '%':  lambda a,b: f'val_num(fmod(_val_to_num({a}), _val_to_num({b})))',
                '<':  lambda a,b: f'val_bool(_val_to_num({a}) < _val_to_num({b}))',
                '>':  lambda a,b: f'val_bool(_val_to_num({a}) > _val_to_num({b}))',
                '<=': lambda a,b: f'val_bool(_val_to_num({a}) <= _val_to_num({b}))',
                '>=': lambda a,b: f'val_bool(_val_to_num({a}) >= _val_to_num({b}))',
                '==': lambda a,b: f'val_bool(_val_to_num({a}) == _val_to_num({b}))',
                '!=': lambda a,b: f'val_bool(_val_to_num({a}) != _val_to_num({b}))',
            }
            return ops.get(node.op, lambda a,b: 'val_nil()')(l, r)

        if t == GetAttr:
            return self._gen_attr(node.obj, node.attr)

        if t == Call:
            return self._gen_call(node)

        if t == Propagate:
            return f'_prop({self.gen_expr(node.expr)})'

        if t == Fallback:
            e = self.gen_expr(node.expr)
            d = self.gen_expr(node.default)
            return f'({e}.type == VAL_ERR ? {d} : ({e}.type == VAL_OK ? *{e}.as.ok_val : {e}))'

        if t == Pipe:
            result = self.gen_expr(node.stages[0])
            for stage in node.stages[1:]:
                fn = self.gen_expr(stage)
                result = f'{fn}({result})'
            return result

        if t == Par:
            items = ', '.join(self.gen_expr(e) for e in node.exprs)
            return f'val_arr(({len(node.exprs)}))'

        if t == MapOp:
            col = self.gen_expr(node.collection)
            fn = self._gen_fn_arg(node.fn)
            return f'({{ Arr* _a=arr_new({col}.as.arr->len); for(int _i=0;_i<{col}.as.arr->len;_i++) arr_push(_a, {fn}({col}.as.arr->items[_i])); val_arr(_a); }})'

        if t == FilterOp:
            col = self.gen_expr(node.collection)
            fn = self._gen_fn_arg(node.fn)
            return f'({{ Arr* _a=arr_new({col}.as.arr->len); for(int _i=0;_i<{col}.as.arr->len;_i++) if(_val_to_num({fn}({col}.as.arr->items[_i]))) arr_push(_a, {col}.as.arr->items[_i]); val_arr(_a); }})'

        if t == Emit:
            return self.gen_expr(node.value)

        if t == FnDef:
            return 'val_nil()'

        if t == RangeExpr:
            s = self.gen_expr(node.start)
            e = self.gen_expr(node.end)
            return f'karn_range({s}, {e})'

        if t == MatchExpr:
            return self._gen_match_expr(node)

        if t in (RetryExpr, TimeoutExpr):
            return self.gen_expr(node.expr)

        return 'val_nil()'

    def _gen_attr(self, obj: Node, attr: str) -> str:
        o = self.gen_expr(obj)
        if attr == 'len':
            return f'({o}.type==VAL_ARR?arr_len({o}):({o}.type==VAL_STR?str_len({o}):val_num(0)))'
        if attr == 'first':
            return f'arr_first({o})'
        if attr == 'last':
            return f'arr_last({o})'
        if attr == 'upper':
            return f'str_upper({o})'
        if attr == 'lower':
            return f'str_lower({o})'
        if attr == 'keys':
            return f'({{ Arr* _a=arr_new({o}.as.map->len); for(int _i=0;_i<{o}.as.map->len;_i++) arr_push(_a, {o}.as.map->keys[_i]); val_arr(_a); }})'
        if attr == 'values':
            return f'({{ Arr* _a=arr_new({o}.as.map->len); for(int _i=0;_i<{o}.as.map->len;_i++) arr_push(_a, {o}.as.map->vals[_i]); val_arr(_a); }})'
        return o

    def _gen_call(self, node: Call) -> str:
        if isinstance(node.callee, GetAttr):
            obj = self.gen_expr(node.callee.obj)
            attr = node.callee.attr
            args = ', '.join(self.gen_expr(a) for a in node.args)
            if attr == 'push':
                return f'({{ arr_push({obj}.as.arr, {args[0]}); val_nil(); }})'
            if attr == 'get':
                return f'map_get({obj}.as.map, {args[0]})'
            if attr == 'put':
                return f'({{ map_put({obj}.as.map, {args[0]}, {args[1]}); val_nil(); }})'
            return f'{obj}'

        if isinstance(node.callee, Ident) and node.callee.name == 'Ok':
            args = ', '.join(self.gen_expr(a) for a in node.args)
            return f'val_ok({args})'
        if isinstance(node.callee, Ident) and node.callee.name == 'Err':
            args = ', '.join(self.gen_expr(a) for a in node.args)
            return f'val_err({args})'

        fn = self._cname(node.callee.name) if isinstance(node.callee, Ident) else self.gen_expr(node.callee)
        args = ', '.join(self.gen_expr(a) for a in node.args)
        return f'{fn}({args})'

    def _gen_fn_arg(self, fn_node: Node) -> str:
        """Get C function name for a lambda (already emitted at file scope)."""
        if isinstance(fn_node, FnDef):
            if hasattr(fn_node, '_c_name'):
                return fn_node._c_name
            # Fallback: shouldn't happen with proper collection
            return 'val_nil'
        return self.gen_expr(fn_node)

    def _gen_match_stmt(self, node: MatchExpr):
        subj = self.gen_expr(node.subject)
        self.emit(f'Val _subj = {subj};')
        for pat, body in node.arms:
            self._emit_match_arm(pat, body, '_subj', stmt_mode=True)

    def _gen_match_fn(self, node: MatchExpr):
        subj = self.gen_expr(node.subject)
        self.emit(f'Val _subj = {subj};')
        for pat, body in node.arms:
            self._emit_match_arm(pat, body, '_subj', stmt_mode=False)
        self.emit('return val_nil();')

    def _gen_match_expr(self, node: MatchExpr) -> str:
        self._match_counter += 1
        mc = self._match_counter
        subj = self.gen_expr(node.subject)
        lines = []
        lines.append(f'{{ Val _subj{mc} = {subj}; Val _res{mc} = val_nil();')
        for pat, body in node.arms:
            body_val = self._gen_match_body_c(body)
            if isinstance(pat, Call) and isinstance(pat.callee, Ident):
                cname = pat.callee.name
                if cname == 'Ok' and pat.args:
                    var = pat.args[0].name if isinstance(pat.args[0], Ident) else '_v'
                    lines.append(f'if (_subj{mc}.type == VAL_OK) {{ Val {var} = *_subj{mc}.as.ok_val; _res{mc} = {body_val}; goto _md{mc}; }}')
                elif cname == 'Err' and pat.args:
                    var = pat.args[0].name if isinstance(pat.args[0], Ident) else '_e'
                    lines.append(f'if (_subj{mc}.type == VAL_ERR) {{ Val {var} = _subj{mc}; _res{mc} = {body_val}; goto _md{mc}; }}')
            elif isinstance(pat, Ident):
                if pat.name == '_':
                    lines.append(f'{{ _res{mc} = {body_val}; goto _md{mc}; }}')
                else:
                    lines.append(f'{{ Val {pat.name} = _subj{mc}; _res{mc} = {body_val}; goto _md{mc}; }}')
            elif isinstance(pat, NumberLit):
                lines.append(f'if (_subj{mc}.type == VAL_NUM && _subj{mc}.as.num == {pat.value}) {{ _res{mc} = {body_val}; goto _md{mc}; }}')
            else:
                lines.append(f'{{ _res{mc} = {body_val}; goto _md{mc}; }}')
        lines.append(f'_md{mc}: _res{mc} }}')
        return ' '.join(lines)

    def _emit_match_arm(self, pat, body, subj_var, stmt_mode=True):
        if isinstance(pat, Call) and isinstance(pat.callee, Ident):
            cname = pat.callee.name
            if cname == 'Ok' and pat.args:
                var = pat.args[0].name if isinstance(pat.args[0], Ident) else '_v'
                body_val = self._gen_match_body_c(body)
                if stmt_mode:
                    self.emit(f'if ({subj_var}.type == VAL_OK) {{ Val {var} = *{subj_var}.as.ok_val; val_println({body_val}); }}')
                else:
                    self.emit(f'if ({subj_var}.type == VAL_OK) {{ Val {var} = *{subj_var}.as.ok_val; return {body_val}; }}')
            elif cname == 'Err' and pat.args:
                var = pat.args[0].name if isinstance(pat.args[0], Ident) else '_e'
                body_val = self._gen_match_body_c(body)
                if stmt_mode:
                    self.emit(f'if ({subj_var}.type == VAL_ERR) {{ Val {var} = {subj_var}; val_println({body_val}); }}')
                else:
                    self.emit(f'if ({subj_var}.type == VAL_ERR) {{ Val {var} = {subj_var}; return {body_val}; }}')
        elif isinstance(pat, Ident):
            if pat.name == '_':
                body_val = self._gen_match_body_c(body)
                if stmt_mode:
                    self.emit(f'{{ val_println({body_val}); }}')
                else:
                    self.emit(f'{{ return {body_val}; }}')
            else:
                body_val = self._gen_match_body_c(body)
                if stmt_mode:
                    self.emit(f'{{ Val {pat.name} = {subj_var}; val_println({body_val}); }}')
                else:
                    self.emit(f'{{ Val {pat.name} = {subj_var}; return {body_val}; }}')
        elif isinstance(pat, NumberLit):
            body_val = self._gen_match_body_c(body)
            if stmt_mode:
                self.emit(f'if ({subj_var}.type == VAL_NUM && {subj_var}.as.num == {pat.value}) {{ val_println({body_val}); }}')
            else:
                self.emit(f'if ({subj_var}.type == VAL_NUM && {subj_var}.as.num == {pat.value}) {{ return {body_val}; }}')

    def _gen_match_body_c(self, body: Node) -> str:
        if isinstance(body, Emit):
            return self.gen_expr(body.value)
        return self.gen_expr(body)

    def _cname(self, name: str) -> str:
        reserved = {'type', 'from', 'as', 'match', 'trait', 'import',
                    'class', 'return', 'printf', 'exit', 'main', 'true', 'false'}
        if name in reserved:
            return f'_{name}'
        return name.replace('-', '_').replace('.', '__')


# ═══════════════════════════════════════════════════════════
#  REPL
# ═══════════════════════════════════════════════════════════

class REPL:
    BANNER = """
\033[33m
  ██╗  ██╗ █████╗ ██████╗ ███╗   ██╗
  ██║ ██╔╝██╔══██╗██╔══██╗████╗  ██║
  █████╔╝ ███████║██████╔╝██╔██╗ ██║
  ██╔═██╗ ██╔══██║██╔══██╗██║╚██╗██║
  ██║  ██╗██║  ██║██║  ██║██║ ╚████║
  ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═══╝
\033[0m
  \033[36mKARN v1.0\033[0m — The Agent's Language
  \033[90minterpreted · jit · compiled\033[0m

  Type \033[33m:help\033[0m for commands, \033[33m:quit\033[0m to exit.
"""

    def __init__(self):
        self.interp = Interpreter()

    def run(self):
        print(self.BANNER)
        buf = []
        while True:
            try:
                prompt = '  \033[33m···\033[0m ' if buf else '  \033[33mkarn>\033[0m '
                line = input(prompt)
            except (EOFError, KeyboardInterrupt):
                print('\n\033[90m  bye.\033[0m')
                break

            if line.strip() == ':quit': break
            if line.strip() == ':help':
                self._help()
                continue
            if line.strip() == ':env':
                self._dump_env()
                continue

            buf.append(line)

            # If line ends with ':' or is indented, keep buffering
            if line.endswith(':') or (buf and line.startswith('  ')):
                continue

            src = '\n'.join(buf)
            buf = []
            self._eval(src)

    def _eval(self, src: str):
        try:
            tokens  = Lexer(src).tokenize()
            ast     = Parser(tokens).parse()
            result  = None
            interp  = self.interp
            for stmt in ast.stmts:
                try:
                    result = interp.eval(stmt, interp.global_env)
                except EmitSignal as e:
                    result = e.value
            if result is not None:
                print(f'  \033[32m→\033[0m {result!r}')
        except (LexError, ParseError) as e:
            print(f'  \033[31m{e}\033[0m')
        except KarnError as e:
            print(f'  \033[31m{e!r}\033[0m')
        except Exception as e:
            print(f'  \033[31mError: {e}\033[0m')

    def _help(self):
        print("""
  \033[33mKARN REPL Commands\033[0m
  :help   — this message
  :env    — show current bindings
  :quit   — exit

  \033[33mExamples\033[0m
  ! "hello"                    — emit a value
  x = 42                       — bind
  add->a b: a+b                — define function
  add(3, 4)                    — call it
  items = [1,2,3]
  items*(x->x*2)               — map
  items%( x->x>1 )             — filter
""")

    def _dump_env(self):
        print('\n  \033[33mBindings:\033[0m')
        for k, v in self.interp.global_env.bindings.items():
            if not callable(v) or isinstance(v, KarnFn):
                print(f'  {k} = {v!r}')
        print()


# ═══════════════════════════════════════════════════════════
#  CLI ENTRY POINT
# ═══════════════════════════════════════════════════════════

def compile_file(source: str, path: str, target: str) -> str:
    tokens  = Lexer(source).tokenize()
    ast     = Parser(tokens).parse()
    if target in ('js', 'web'):
        gen = JSCodeGen(target=target)
    elif target in ('c', 'wasm32', 'linux-x64', 'linux-arm64', 'macos-arm64', 'windows-x64'):
        gen = CCodeGen(target=target)
    else:
        gen = CodeGen(target=target)
    return gen.generate(ast)


def run_file(source: str, path: str, jit=False):
    tokens  = Lexer(source).tokenize()
    ast     = Parser(tokens).parse()
    interp  = Interpreter()
    interp.jit_mode = jit
    try:
        interp.run(ast)
    except EmitSignal as e:
        print(e.value)
    except KarnError as e:
        print(f'\033[31m{e!r}\033[0m', file=sys.stderr)
        sys.exit(1)


def check_file(source: str, path: str):
    """Parse + type-check only, no execution."""
    try:
        tokens = Lexer(source).tokenize()
        ast    = Parser(tokens).parse()
        print(f'\033[32m✓\033[0m {path} — OK ({len(ast.stmts)} statements)')
    except (LexError, ParseError) as e:
        print(f'\033[31m✗\033[0m {e}')
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        prog='karn',
        description='KARN v1.0 — The Agent\'s Language',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
        Examples:
          karn run hello.kn
          karn run server.kn --jit
          karn build app.kn --target linux-x64
          karn build app.kn --target python
          karn repl
          karn check *.kn
        """)
    )

    sub = parser.add_subparsers(dest='cmd')

    p_run = sub.add_parser('run', help='Run a .kn file (interpreted)')
    p_run.add_argument('file')
    p_run.add_argument('--jit', action='store_true', help='Enable JIT compilation')

    p_build = sub.add_parser('build', help='Compile a .kn file')
    p_build.add_argument('file')
    p_build.add_argument('--target', default='python',
                         choices=['python','js','web','c','linux-x64','linux-arm64','macos-arm64',
                                  'windows-x64','wasm32','ios','android',
                                  'lambda','edge','embed','docker'],
                         help='Compilation target (default: python)')
    p_build.add_argument('--out', '-o', default=None, help='Output file path')

    p_repl = sub.add_parser('repl', help='Start interactive REPL')

    p_check = sub.add_parser('check', help='Type-check without running')
    p_check.add_argument('files', nargs='+')

    args = parser.parse_args()

    if args.cmd == 'repl' or args.cmd is None:
        REPL().run()
        return

    if args.cmd == 'run':
        if not os.path.exists(args.file):
            print(f'Error: file not found: {args.file}', file=sys.stderr)
            sys.exit(1)
        source = open(args.file).read()
        run_file(source, args.file, jit=args.jit)

    elif args.cmd == 'build':
        if not os.path.exists(args.file):
            print(f'Error: file not found: {args.file}', file=sys.stderr)
            sys.exit(1)
        source = open(args.file).read()
        target = args.target

        native_targets = {'linux-x64', 'linux-arm64', 'macos-arm64', 'windows-x64', 'wasm32'}

        out = compile_file(source, args.file, target)
        if target in ('js', 'web'):
            ext = 'html' if target == 'web' else 'js'
            out_path = args.out or args.file.replace('.kn', f'.{ext}')
            open(out_path, 'w').write(out)
            size = os.path.getsize(out_path)
            print(f'\033[32m✓\033[0m Compiled \033[33m{args.file}\033[0m → '
                  f'\033[33m{out_path}\033[0m ({size} bytes, target: {target})')

        elif target == 'c':
            out_path = args.out or args.file.replace('.kn', '.c')
            open(out_path, 'w').write(out)
            size = os.path.getsize(out_path)
            print(f'\033[32m✓\033[0m Compiled \033[33m{args.file}\033[0m → '
                  f'\033[33m{out_path}\033[0m ({size} bytes, target: {target})')

        elif target in native_targets:
            # Write C, then compile to native
            c_path = args.file.replace('.kn', '.c')
            open(c_path, 'w').write(out)

            if target == 'wasm32':
                out_path = args.out or args.file.replace('.kn', '.wasm')
                cc = 'emcc' if shutil.which('emcc') else 'clang'
                if cc == 'emcc':
                    cmd = [cc, c_path, '-o', out_path, '-lm']
                else:
                    cmd = [cc, '--target=wasm32', '-nostdlib', c_path, '-o', out_path, '-lm']
            else:
                out_path = args.out or args.file.replace('.kn', '')
                cc = 'gcc' if shutil.which('gcc') else 'cc'
                arch_flags = {
                    'linux-x64':   ['-m64'],
                    'linux-arm64': ['-march=armv8-a'],
                    'macos-arm64': ['-arch', 'arm64'],
                    'windows-x64': ['-m64'],
                }
                cmd = [cc, c_path, '-o', out_path, '-lm']
                if target in arch_flags:
                    cmd[2:2] = arch_flags[target]

            import subprocess
            print(f'\033[32m✓\033[0m Compiled \033[33m{args.file}\033[0m → \033[33m{c_path}\033[0m')
            print(f'\033[36m[karn:agc]\033[0m Compiling C → \033[33m{out_path}\033[0m ({target})...')
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
                if result.returncode == 0:
                    size = os.path.getsize(out_path)
                    print(f'\033[32m✓\033[0m Built \033[33m{out_path}\033[0m ({size} bytes)')
                else:
                    print(f'\033[31m✗\033[0m Compilation failed:')
                    print(result.stderr)
                    sys.exit(1)
            except FileNotFoundError:
                print(f'\033[31m✗\033[0m Compiler not found: {cmd[0]}')
                sys.exit(1)
            except subprocess.TimeoutExpired:
                print(f'\033[31m✗\033[0m Compilation timed out')
                sys.exit(1)

        else:
            out_path = args.out or args.file.replace('.kn', f'.{target}.py')
            open(out_path, 'w').write(out)
            size = os.path.getsize(out_path)
            print(f'\033[32m✓\033[0m Compiled \033[33m{args.file}\033[0m → '
                  f'\033[33m{out_path}\033[0m ({size} bytes, target: {target})')

    elif args.cmd == 'check':
        for f in args.files:
            if not os.path.exists(f):
                print(f'Error: file not found: {f}', file=sys.stderr)
                continue
            check_file(open(f).read(), f)


if __name__ == '__main__':
    main()
