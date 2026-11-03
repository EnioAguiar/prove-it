"""Static quality gate for LLM-generated pytest files (AST only, nothing runs)."""

import ast
from dataclasses import dataclass, field

TARGET_MODULE = "target"
FORBIDDEN_MODULES = frozenset({"os", "subprocess", "socket"})


@dataclass
class GateResult:
    ok: bool
    reasons: list[str] = field(default_factory=list)


def check_tests(test_src: str, user_src: str) -> GateResult:
    try:
        tree = ast.parse(test_src)
    except (SyntaxError, ValueError) as exc:
        return GateResult(ok=False, reasons=[f"tests do not parse: {exc}"])

    public = _public_functions(user_src)
    imported, bare_names, module_aliases = _target_bindings(tree, public)
    functions = [n for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    tests = [f for f in functions if f.name.startswith("test_")]
    reasons: list[str] = []

    for module in sorted(_forbidden_imports(tree)):
        reasons.append(f"forbidden import of {module}")
    if not imported:
        reasons.append(f"tests do not import from {TARGET_MODULE}")
    if not any(_calls_public(t, public, bare_names, module_aliases) for t in tests):
        reasons.append("no test function calls a public function from the user code")
    if not any(_is_given(d) for f in functions for d in f.decorator_list):
        reasons.append("no function is decorated with hypothesis given")
    for t in tests:
        if _is_trivial(t):
            reasons.append(f"{t.name} only contains assert True or pass")

    return GateResult(ok=not reasons, reasons=reasons)


def _public_functions(user_src: str) -> set[str]:
    try:
        tree = ast.parse(user_src)
    except (SyntaxError, ValueError):
        return set()
    return {n.name for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and not n.name.startswith("_")}


def _target_bindings(tree: ast.Module, public: set[str]) -> tuple[bool, set[str], set[str]]:
    """Whether target is imported, names bound to its public functions, module aliases."""
    bare: set[str] = set()
    modules: set[str] = set()
    imported = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module == TARGET_MODULE:
            imported = True
            for alias in node.names:
                if alias.name == "*":
                    bare |= public
                elif alias.name in public:
                    bare.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == TARGET_MODULE:
                    imported = True
                    modules.add(alias.asname or alias.name)
    return imported, bare, modules


def _calls_public(fn: ast.AST, public: set[str], bare: set[str], modules: set[str]) -> bool:
    for stmt in fn.body:
        for node in ast.walk(stmt):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name) and func.id in bare:
                return True
            if (isinstance(func, ast.Attribute) and func.attr in public
                    and isinstance(func.value, ast.Name) and func.value.id in modules):
                return True
    return False


def _is_given(decorator: ast.expr) -> bool:
    if isinstance(decorator, ast.Call):
        decorator = decorator.func
    if isinstance(decorator, ast.Name):
        return decorator.id == "given"
    return (isinstance(decorator, ast.Attribute) and decorator.attr == "given"
            and isinstance(decorator.value, ast.Name) and decorator.value.id == "hypothesis")


def _is_trivial(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for stmt in fn.body:
        if isinstance(stmt, ast.Pass):
            continue
        if (isinstance(stmt, ast.Assert) and isinstance(stmt.test, ast.Constant)
                and stmt.test.value is True):
            continue
        if (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant)
                and isinstance(stmt.value.value, str)):
            continue  # docstring
        return False
    return True


def _forbidden_imports(tree: ast.Module) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module]
        else:
            continue
        for name in names:
            root = name.split(".")[0]
            if root in FORBIDDEN_MODULES:
                found.add(root)
    return found
