"""Regression tests for indivisible resources, offline / no PostgreSQL."""
from decimal import Decimal
import pytest
import routing_lab6 as routing


@pytest.fixture
def product_units(monkeypatch):
    p = {'id': 84, 'name': 'Роза (тест)',
         'unit': {'name': 'штука', 'symbol': 'шт.', 'is_integer_only': True}}
    monkeypatch.setattr(routing, 'input_product', lambda db, source_id: p)
    return p


def test_fractional_flower_norm_rejected(product_units):
    with pytest.raises(routing.RoutingError) as exc:
        routing.check_indivisible_input(None, 26, Decimal('0.5'), Decimal('1'))
    assert exc.value.code == 'FRACTIONAL_INDIVISIBLE_RESOURCE'
    assert exc.value.status == 422


def test_fractional_flower_norm_per_unit_rejected(product_units):
    with pytest.raises(routing.RoutingError) as exc:
        routing.check_indivisible_input(None, 26, Decimal('1'), Decimal('2'))
    assert exc.value.code == 'FRACTIONAL_INDIVISIBLE_RESOURCE'


def test_whole_flower_allowed(product_units):
    routing.check_indivisible_input(None, 26, Decimal('5'), Decimal('1'))
    routing.check_indivisible_input(None, 26, Decimal('2'), Decimal('2'))


def test_mr_q_cannot_return_half_flower(product_units):
    with pytest.raises(routing.RoutingError) as exc:
        routing.check_indivisible_source_total(None, 26, Decimal('0.5'))
    assert exc.value.code == 'FRACTIONAL_INDIVISIBLE_RESOURCE'


def test_fractional_ribbon_allowed(product_units):
    product_units['id'] = 88
    product_units['name'] = 'Лента'
    product_units['unit']['is_integer_only'] = False
    routing.check_indivisible_input(None, 26, Decimal('0.5'), Decimal('1'))
    routing.check_indivisible_source_total(None, 26, Decimal('0.5'))
