"""Workflow data interpolation stays literal and bounded before allocation."""
import pytest
from agent.workflow_runtime import _render
from hermes_state_runtime import RuntimeStoreError


def test_interpolation_is_literal_nonrecursive_and_uses_utf8_byte_bound():
    template = '# Greeting\n${input.name}\n${steps.first}'
    result = _render(template, {'name':'${input.secret}'}, {'first':'café'}, maximum=64)
    assert result == '# Greeting\n${input.secret}\ncafé'.encode()
    assert _render('${input.name}', {'name':'éé'}, {}, maximum=4) == 'éé'.encode()
    with pytest.raises(RuntimeStoreError) as error:
        _render('${input.name}', {'name':'éé'}, {}, maximum=3)
    assert error.value.code == 'workflow_output_bound'


def test_step_substitution_rejects_expansion_before_joining_a_large_result():
    # A large repeated prior result used to allocate the whole expansion before
    # checking its bound. The interpreter now bounds every appended UTF-8 chunk.
    with pytest.raises(RuntimeStoreError) as error:
        _render('${steps.first}' * 5000, {}, {'first':'x' * 4096}, maximum=8192)
    assert error.value.code == 'workflow_output_bound'
