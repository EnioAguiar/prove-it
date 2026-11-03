from app.test_gate import GateResult, check_tests

USER_SRC = '''
def add(a, b):
    return a + b


def _helper():
    return 1
'''

GOOD_TESTS = '''
from target import *
from hypothesis import given, strategies as st


def test_add_small():
    assert add(1, 2) == 3


@given(st.integers(), st.integers())
def test_add_commutes(a, b):
    assert add(a, b) == add(b, a)
'''


def test_accepts_good_tests():
    assert check_tests(GOOD_TESTS, USER_SRC) == GateResult(ok=True, reasons=[])


def test_accepts_module_import_and_qualified_given():
    src = '''
import hypothesis
import target
from hypothesis import strategies as st


@hypothesis.given(st.integers())
def test_add_zero(a):
    assert target.add(a, 0) == a
'''
    assert check_tests(src, USER_SRC).ok


def test_accepts_call_nested_in_comprehension():
    src = '''
from target import *
from hypothesis import given, strategies as st


@given(st.lists(st.integers()))
def test_many(xs):
    assert all(r == x for r, x in [(add(x, 0), x) for x in xs])
'''
    assert check_tests(src, USER_SRC).ok


def test_rejects_assert_true_only():
    src = GOOD_TESTS + '''

def test_trivial():
    assert True


def test_empty():
    """Nothing here."""
    pass
'''
    result = check_tests(src, USER_SRC)
    assert not result.ok
    assert any("test_trivial" in r for r in result.reasons)
    assert any("test_empty" in r for r in result.reasons)


def test_rejects_missing_given():
    src = '''
from target import *


def test_add_small():
    assert add(1, 2) == 3
'''
    result = check_tests(src, USER_SRC)
    assert not result.ok
    assert any("given" in r for r in result.reasons)


def test_rejects_not_calling_user_function():
    src = '''
from target import *
from hypothesis import given, strategies as st


@given(st.integers())
def test_private_only(a):
    assert _helper() == 1
    assert abs(a) >= 0
'''
    result = check_tests(src, USER_SRC)
    assert not result.ok
    assert any("public function" in r for r in result.reasons)


def test_rejects_missing_target_import():
    src = '''
from hypothesis import given, strategies as st


@given(st.integers())
def test_add(a):
    assert add(a, 0) == a
'''
    result = check_tests(src, USER_SRC)
    assert not result.ok
    assert any("target" in r for r in result.reasons)


def test_rejects_syntax_error():
    result = check_tests("def test_x(:\n    pass\n", USER_SRC)
    assert not result.ok
    assert len(result.reasons) == 1
    assert "parse" in result.reasons[0]


def test_rejects_os_import():
    for line in ("import os", "import os.path", "from os import path",
                 "import subprocess", "from socket import socket"):
        result = check_tests(line + "\n" + GOOD_TESTS, USER_SRC)
        assert not result.ok, line
        assert any("forbidden" in r for r in result.reasons), line


def test_unparseable_user_src_does_not_raise():
    result = check_tests(GOOD_TESTS, "def broken(:\n")
    assert isinstance(result, GateResult)
    assert not result.ok
    assert result.reasons == ["user code does not parse"]


CLASS_SRC = '''
class Stack:
    def __init__(self):
        self.items = []

    def push(self, x):
        self.items.append(x)
'''


def test_accepts_class_only_user_code():
    star = '''
from target import *
from hypothesis import given, strategies as st


@given(st.integers())
def test_push(x):
    s = Stack()
    s.push(x)
    assert s.items == [x]
'''
    qualified = '''
import target
from hypothesis import given, strategies as st


@given(st.integers())
def test_push(x):
    s = target.Stack()
    s.push(x)
    assert s.items[-1] == x
'''
    assert check_tests(star, CLASS_SRC) == GateResult(ok=True, reasons=[])
    assert check_tests(qualified, CLASS_SRC) == GateResult(ok=True, reasons=[])


def test_rejects_ellipsis_and_truthy_constant_asserts():
    src = GOOD_TESTS + '''

def test_ellipsis():
    ...


def test_assert_one():
    assert 1
'''
    result = check_tests(src, USER_SRC)
    assert not result.ok
    assert any("test_ellipsis" in r for r in result.reasons)
    assert any("test_assert_one" in r for r in result.reasons)
