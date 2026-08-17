"""Guard analyst system messages against the tuple-instead-of-string bug.

A trailing comma after the string concatenation once turned the fundamentals
analyst's ``system_message`` into a one-element tuple, so the prompt rendered
as ``("...",)`` and the language instruction lost its effect.
"""
import ast
import inspect

import pytest

import tradingagents.agents.analysts.fundamentals_analyst as fa
import tradingagents.agents.analysts.market_analyst as ma
import tradingagents.agents.analysts.news_analyst as na
import tradingagents.agents.analysts.sentiment_analyst as sa

_ANALYST_MODULES = [fa, ma, na, sa]


def _system_message_assignments(module):
    tree = ast.parse(inspect.getsource(module))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "system_message":
                    yield node.value


@pytest.mark.unit
def test_system_message_is_never_a_tuple():
    for module in _ANALYST_MODULES:
        assignments = list(_system_message_assignments(module))
        assert assignments, f"no system_message assignment found in {module.__name__}"
        for value in assignments:
            assert not isinstance(value, ast.Tuple), (
                f"{module.__name__}: system_message is a tuple — "
                "check for a stray trailing comma"
            )
