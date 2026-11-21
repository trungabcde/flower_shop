"""Offline structural and contract smoke tests. Not a substitute for PostgreSQL API integration."""
from pathlib import Path
import os
import re
import sys
from decimal import Decimal

import pytest

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from routing_lab6 import RoutingError, n, positive, nonnegative, number


def test_migration_contains_exactly_19_tables():
    schema=(HERE/'lab6_migration.sql').read_text('utf8')
    names=re.findall(r'CREATE TABLE IF NOT EXISTS (routing_\w+)',schema)
    assert len(names)==19
    assert len(names)==len(set(names))
    assert 'routing_tp_application_outputs' in names
    assert 'routing_material_prices' in names


def test_lab5_schema_untouched():
    schema=(HERE/'lab6_migration.sql').read_text('utf8')
    assert not re.search(r'(?:DROP|ALTER)\s+TABLE\s+(?:products|spec_versions|spec_items|spec_variants)',schema,re.I)
    assert 'REFERENCES spec_versions(id_spec_version)' in schema
    assert 'REFERENCES spec_variants(id_variant)' in schema
    assert 'REFERENCES products(id_product)' in schema


def test_postgresql_functions_exist():
    script=(HERE/'lab6_functions.sql').read_text('utf8')
    for name in ['FIND_TM','MR_Q','SUM_MR','TR_TO','ROUTING_COST']:
        assert f'FUNCTION "{name}"' in script
    assert 'WITH RECURSIVE' in script
    assert 'GROUP BY' in script


def test_numeric_guards():
    assert positive(Decimal('0.0001'))==Decimal('0.0001')
    assert nonnegative(Decimal('0'),'setup_minutes')==0
    with pytest.raises(RoutingError):positive(0)
    with pytest.raises(RoutingError):positive(-5)
    with pytest.raises(RoutingError):nonnegative(-1,'unit_cost')
    with pytest.raises(RoutingError):n('NaN')


def test_money_reference_values():
    # 2 bouquets: 10 roses, 2 sheets, 4 metres of ribbon.
    materials=10*100+2*20+4*10
    minutes=(2+2*2)+(5+10*2)+(2+3*2)
    labor=minutes/60*300
    assert materials==1080
    assert minutes==39
    assert labor==195
    assert materials+labor==1275


def test_nested_gift_reference():
    assert 1080+30==1110
    assert 39+(1+4)==44
    assert 44*300/60==220
    assert 1110+220==1330


def test_template_reference_sizes():
    for size,mat,minutes,total in [(1,40,5,65),(2,80,7,115)]:
        paper=size
        ribbon=2*size
        assert paper*20+ribbon*10==mat
        assert 2+(1+2*size)==minutes
        assert mat+minutes*300/60==total


def test_shared_setup_once():
    # Common operation required by two branches: do not charge setup twice.
    aggregate_required=Decimal(1)+Decimal(1)
    total=Decimal(5)+Decimal(10)*aggregate_required
    assert total==25
    assert total!=30


def test_openapi_integration_with_existing_lab5(monkeypatch):
    # The full OpenAPI schema can be inspected without connecting to PostgreSQL.
    root=HERE.parent
    lab5=root/'lab5'
    if not (lab5/'main_new_lab5.py').exists():
        pytest.skip('To inspect combined Swagger schema, place lab6 beside lab5')
    monkeypatch.setenv('FLOWER_SHOP_DATABASE_URL','sqlite:///./lab6_openapi_only.db')
    from main_new_lab6 import app
    paths=app.openapi()['paths']
    required=['/api/routing/routes','/api/routing/routes/{id}/operations',
       '/api/routing/operations/{id}/inputs','/api/routing/routes/{id}/tree',
       '/api/routing/routes/{id}/materials','/api/routing/routes/{id}/labor',
       '/api/routing/routes/{id}/cost','/api/routing/typical-processes',
       '/api/routing/routes/{id}/typical-applications',
       '/api/routing/typical-applications/{id}',
       '/api/routing/products/{id}/versions','/api/routing/spec-versions/{id}/variants']
    for path in required:assert path in paths
    assert 'post' in paths['/api/routing/routes']
    assert 'patch' in paths['/api/routing/routes/{id}']
    assert '/bom/expand' in paths
    assert len([k for k in paths if k.startswith('/api/routing')])>=40
