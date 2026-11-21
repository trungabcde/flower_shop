"""Lab 6 routing (PostgreSQL) for Flower Shop Lab 5.

Deliberately reuses products / BOM from Lab 5. Database schema and DB functions
are installed by separate idempotent SQL scripts; imports never migrate data.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, DBAPIError
from sqlalchemy.orm import Session


class RoutingError(Exception):
    def __init__(self, code: str, message: str, status: int = 422, obj: dict | None = None):
        self.code, self.message, self.status, self.obj = code, message, status, obj
        super().__init__(message)


def err(code: str, msg: str, status: int = 422, **context):
    raise RoutingError(code, msg, status, context or None)


def n(value):
    v = Decimal(str(value))
    if not v.is_finite():
        err('INVALID_NUMBER', 'Число должно быть конечным')
    return v


def positive(v, field: str = 'quantity'):
    x = n(v)
    if x <= 0:
        err('INVALID_QUANTITY' if field == 'quantity' else 'INVALID_FOR_QUANTITY',
            f'Поле «{field}» должно быть больше нуля.', field=field)
    return x


def nonnegative(v, field: str):
    x = n(v)
    if x < 0:
        err('INVALID_VALUE', f'Поле «{field}» не может быть отрицательным.', field=field)
    return x


def number(x):
    if isinstance(x, Decimal):
        return int(x) if x == x.to_integral_value() else float(x)
    if isinstance(x, dict):
        return {k: number(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [number(v) for v in x]
    return x


def one(db, sql: str, **params):
    r = db.execute(text(sql), params).mappings().first()
    return dict(r) if r else None


def many(db, sql: str, **params):
    return [dict(r) for r in db.execute(text(sql), params).mappings().all()]


def scalar(db, sql: str, **params):
    return db.execute(text(sql), params).scalar()


def require(db, table, id, what='Запись'):
    # All table identifiers passed here are internal constants, never user input.
    row = one(db, f'SELECT * FROM {table} WHERE id=:id', id=id)
    if not row:
        err('NOT_FOUND', f'{what} с ID={id} не найдена.', 404, id=id, entity=what)
    return row


def insert(db, table, payload):
    if not payload:
        err('EMPTY_DATA', 'Не переданы поля для сохранения')
    names = list(payload)
    placeholders = ','.join(':' + s for s in names)
    row = one(db, f"INSERT INTO {table} ({','.join(names)}) VALUES ({placeholders}) RETURNING *", **payload)
    return row


def change(db, table, id, payload):
    if not payload:
        return require(db, table, id)
    assignments = ','.join(f'{k}=:{k}' for k in payload)
    row = one(db, f'UPDATE {table} SET {assignments} WHERE id=:__id RETURNING *', __id=id, **payload)
    if not row:
        err('NOT_FOUND', f'Запись {table}/{id} не найдена.', 404)
    return row


def remove(db, table, id):
    found = scalar(db, f'DELETE FROM {table} WHERE id=:id RETURNING id', id=id)
    if found is None:
        err('NOT_FOUND', f'Запись {table}/{id} не найдена.', 404)


def obj(db, table, id, label='name'):
    if id is None:
        return None
    r = one(db, f'SELECT id,{label} AS name FROM {table} WHERE id=:id', id=id)
    if r is None:
        err('INVALID_REFERENCE', f'Не найдена запись {table}/{id}.', 404)
    return r


def product(db, pid):
    r = one(db, '''SELECT p.id_product AS id,p.name,p.code,u.name AS unit_name,
        u.short_name AS unit_symbol,u.is_integer_only
        FROM products p LEFT JOIN units u ON u.id_unit=p.unit_id
        WHERE p.id_product=:id''', id=pid)
    if not r:
        err('PRODUCT_NOT_FOUND', f'Товар {pid} не найден.', 404, product_id=pid)
    return {'id':r['id'],'name':r['name'],'code':r['code'],
            'unit':{'name':r['unit_name'],'symbol':r['unit_symbol'],
                    'is_integer_only':r['is_integer_only']}}


def version(db, vid):
    r=one(db,'SELECT * FROM spec_versions WHERE id_spec_version=:id',id=vid)
    if not r:
        err('VERSION_NOT_FOUND', f'Версия спецификации {vid} не найдена.', 404)
    return {'id':r['id_spec_version'],'version_num':r['version_num'],
            'product':product(db,r['product_id']),'for_quantity':number(r['for_quantity']),
            'base_version_id':r['base_version_id']}


def route_info(db, rid):
    r=require(db,'routing_routes',rid,'Маршрут')
    return {'id':r['id'],'name':r['name'],
       'product':version(db,r['spec_version_id'])['product'],
       'spec_version':{'id':r['spec_version_id'],
                       'version_num':version(db,r['spec_version_id'])['version_num']},
       'variant':variant_info(db,r['variant_id']) if r['variant_id'] is not None else None,
       'final_operation':operation_brief(db,r['final_node_id']) if r['final_node_id'] is not None else None}


def variant_info(db, vid):
    r=one(db,'SELECT id_variant AS id,name FROM spec_variants WHERE id_variant=:id',id=vid)
    if not r:
        err('VARIANT_NOT_FOUND',f'Вариант {vid} не найден.',404)
    return r


def operation_brief(db, nid):
    r=require(db,'routing_nodes',nid,'Операция')
    if r['kind']!='production':
        err('INVALID_OPERATION',f'Узел {nid} является покупным материалом, а не операцией.')
    return {'id':r['id'],'number':r['number'],'name':r['operation_name'],
            'result_name':r['state_name']}


def operation_info(db, nid):
    r=require(db,'routing_nodes',nid,'Операция')
    if r['kind']!='production':
        err('INVALID_OPERATION',f'Узел {nid} не является производственной операцией.')
    return {**operation_brief(db,nid),'route':{'id':r['route_id'],
       'name':require(db,'routing_routes',r['route_id'])['name']},
       'description':r['description'],
       'operation_type':obj(db,'routing_operation_types',r['operation_type_id']),
       'work_center':obj(db,'routing_work_centers',r['work_center_id']),
       'profession':obj(db,'routing_professions',r['profession_id']),
       'qualification':obj(db,'routing_qualifications',r['qualification_id']),
       'setup_minutes':number(r['setup_minutes']), 'unit_minutes':number(r['unit_minutes']),
       'application_id':r['application_id'],
       'template_operation_id':r['template_operation_id']}


def source_info(db, nid):
    r=require(db,'routing_nodes',nid,'Источник')
    if r['kind']=='purchase':
        p=product(db,r['purchase_product_id'])
        return {'id':nid,'kind':'purchase','kind_name':'Покупной материал',
                'product':{'id':p['id'],'name':p['name']},
                'unit':p['unit']['symbol']}
    op=operation_brief(db,nid)
    route=route_info_light(db,r['route_id'])
    return {'id':nid,'kind':'operation_result','kind_name':'Результат операции',
            'operation':{'id':nid,'name':op['name']},'result_name':op['result_name'],
            'route':{'id':route['id'],'name':route['name']},
            'product':{'id':route['product']['id'],'name':route['product']['name']},
            'unit':route['product']['unit']['symbol']}


def route_info_light(db,rid):
    r=require(db,'routing_routes',rid,'Маршрут')
    v=version(db,r['spec_version_id'])
    return {'id':rid,'name':r['name'],'product':v['product']}


def input_info(db, operation_id, source_id):
    r=one(db,'SELECT * FROM routing_inputs WHERE output_node_id=:op AND input_node_id=:src',op=operation_id,src=source_id)
    if not r:
        err('NOT_FOUND','Связь входного ресурса не найдена.',404)
    return {'consumer':operation_brief(db,operation_id),'source':source_info(db,source_id),
            'quantity':number(r['quantity']),'for_quantity':number(r['for_quantity'])}


def tool_info(db,operation_id,tool_id):
    return {'operation':operation_brief(db,operation_id),
            'tool':obj(db,'routing_tools',tool_id)}


def require_mutable_route(db,rid):
    r=require(db,'routing_routes',rid,'Маршрут')
    if r['final_node_id'] and scalar(db,'''SELECT 1 FROM routing_inputs i
       JOIN routing_nodes out ON out.id=i.output_node_id
       WHERE i.input_node_id=:final AND out.route_id<>:rid LIMIT 1''',final=r['final_node_id'],rid=rid):
        err('ROUTE_IN_USE',f'Маршрут «{r["name"]}» уже используется другим маршрутом.',409,route={'id':rid,'name':r['name']})
    return r


def require_mutable_operation(db,nid):
    r=require(db,'routing_nodes',nid,'Операция')
    if r['kind']!='production':
        err('INVALID_OPERATION','Ожидалась производственная операция.')
    if r['application_id']:
        err('TEMPLATE_OPERATION_LOCKED','Операция создана из типового процесса; измените параметры применения.',409)
    require_mutable_route(db,r['route_id'])
    return r


def require_mutable_template(db,process_id):
    r=require(db,'routing_typical_processes',process_id,'Типовой процесс')
    if scalar(db,'SELECT 1 FROM routing_tp_applications WHERE process_id=:id LIMIT 1',id=process_id):
        err('TEMPLATE_IN_USE',f'Типовой процесс «{r["name"]}» уже используется.',409,process={'id':process_id,'name':r['name']})
    return r


def check_no_cycle(db,output_id,input_id):
    if output_id==input_id:
        err('CYCLE_DETECTED','Операция не может ссылаться сама на себя.',409)
    rows=many(db,'SELECT output_node_id,input_node_id FROM routing_inputs')
    graph=defaultdict(list)
    for r in rows:
        graph[r['output_node_id']].append(r['input_node_id'])
    pending=[input_id]; seen=set()
    while pending:
        nid=pending.pop()
        if nid==output_id:
            err('CYCLE_DETECTED','Входная зависимость создаёт цикл операций.',409)
        if nid not in seen:
            seen.add(nid); pending.extend(graph[nid])


def buy_node(db,product_id):
    p=product(db,product_id)
    found=one(db,"SELECT id FROM routing_nodes WHERE kind='purchase' AND purchase_product_id=:id",id=product_id)
    if found:return found['id']
    return insert(db,'routing_nodes',{'kind':'purchase','purchase_product_id':product_id})['id']


def validate_source(db, consumer_id, src_id):
    dst=require(db,'routing_nodes',consumer_id,'Операция')
    src=require(db,'routing_nodes',src_id,'Источник')
    if dst['kind']!='production':err('INVALID_OPERATION','Вход можно назначить только производственной операции.')
    require_mutable_route(db,dst['route_id'])
    if src['kind']=='production' and dst['route_id']!=src['route_id']:
        other=require(db,'routing_routes',src['route_id'],'Маршрут источника')
        if other['final_node_id']!=src_id:
            err('INVALID_EXTERNAL_INPUT','Из другого маршрута разрешён только его итоговый результат.',422)
        # Require a fully defined source route.
        scalar(db,'SELECT COUNT(*) FROM "FIND_TM"(:rid,1)',rid=src['route_id'])
    check_no_cycle(db,consumer_id,src_id)


def input_product(db, source_id):
    """Product supplying the unit for either purchase or production input."""
    source=require(db,'routing_nodes',source_id,'Источник')
    if source['kind']=='purchase':
        return product(db,source['purchase_product_id'])
    return route_info_light(db,source['route_id'])['product']


def check_indivisible_input(db,source_id,quantity,for_quantity):
    """An indivisible resource must be whole for one output unit.

    For a consumption norm Q/FOR_Q, checking Q alone is insufficient:
    Q=1, FOR_Q=2 would still consume half a flower per finished bouquet.
    """
    q=positive(quantity)
    base=positive(for_quantity,'for_quantity')
    item=input_product(db,source_id)
    norm=q/base
    if item['unit']['is_integer_only'] and norm!=norm.to_integral_value():
        err('FRACTIONAL_INDIVISIBLE_RESOURCE',
            f'Для ресурса «{item["name"]}» требуется целое количество на единицу выпуска; задано {norm}.',
            422,product={'id':item['id'],'name':item['name']},
            quantity=number(q),for_quantity=number(base))


def check_indivisible_source_total(db,source_id,quantity):
    """Protect MR_Q responses against fractional totals in legacy records."""
    item=input_product(db,source_id)
    qty=n(quantity)
    if item['unit']['is_integer_only'] and qty!=qty.to_integral_value():
        err('FRACTIONAL_INDIVISIBLE_RESOURCE',
            f'Для ресурса «{item["name"]}» нельзя использовать дробное количество {qty}.',
            422,product={'id':item['id'],'name':item['name']})


def check_material_units(db,items):
    for r in items:
        p=product(db,r['product_id'])
        qty=n(r['quantity'])
        if p['unit']['is_integer_only'] and qty!=qty.to_integral_value():
            err('FRACTIONAL_INDIVISIBLE_RESOURCE',
                f'Для ресурса «{p["name"]}» нельзя использовать дробное количество {qty}.',
                product={'id':p['id'],'name':p['name']})


class RouteCreate(BaseModel):
    name: str = Field(...,examples=['Изготовление учебного букета'])
    spec_version_id: int = Field(...,description='ID из GET /api/routing/products/{id}/versions')
    variant_id: int | None = Field(None,description='ID из GET /api/routing/spec-versions/{id}/variants')

class RoutePatch(BaseModel):
    name: str | None=None
    final_operation_id: int | None=None

class OperationCreate(BaseModel):
    number: int = 10
    name: str='Подготовить цветы'
    result_name: str='Подготовленные цветы'
    description: str | None=None
    operation_type_id: int=Field(1,description='GET /api/routing/operation-types')
    work_center_id: int=Field(1,description='GET /api/routing/work-centers')
    profession_id: int=Field(1,description='GET /api/routing/professions')
    qualification_id: int=Field(1,description='GET /api/routing/qualifications')
    setup_minutes: Decimal=Decimal(2)
    unit_minutes: Decimal=Decimal(2)

class OperationPatch(BaseModel):
    number: int|None=None
    name: str|None=None
    result_name: str|None=None
    description: str|None=None
    operation_type_id: int|None=None
    work_center_id: int|None=None
    profession_id: int|None=None
    qualification_id: int|None=None
    setup_minutes: Decimal|None=None
    unit_minutes: Decimal|None=None

class SourceChoice(BaseModel):
    kind: str = 'purchase'  # purchase/operation_result
    product_id: int|None=None
    operation_id: int|None=None

class InputCreate(BaseModel):
    source: SourceChoice
    quantity: Decimal=Decimal(1)
    for_quantity: Decimal=Decimal(1)

class InputPatch(BaseModel):
    quantity: Decimal | None=None
    for_quantity: Decimal | None=None

class ToolLink(BaseModel):
    tool_id: int=Field(1,description='GET /api/routing/tools')


RESOURCE_EXAMPLES={
 'departments':{'code':'workshop','name':'Флористическая мастерская'},
 'work-centers':{'code':'preparation','name':'Станция подготовки цветов',
                 'type_name':'Рабочее место флориста','department_id':1},
 'operation-types':{'code':'preparation','name':'Подготовка','parent_id':None},
 'professions':{'code':'florist','name':'Флорист','parent_id':None,'base_hourly_rate':300},
 'qualifications':{'code':'simple','name':'Работа с простыми изделиями',
                   'coefficient':1,'sort_order':1},
 'tools':{'code':'secateur','name':'Секатор','kind':'tool'},
}

RESOURCE_FIELDS={
 'departments':('routing_departments',('code','name'),('code','name')),
 'work-centers':('routing_work_centers',('code','name','type_name','department_id'),('code','name','type_name','department_id')),
 'operation-types':('routing_operation_types',('code','name','parent_id'),('code','name')),
 'professions':('routing_professions',('code','name','parent_id','base_hourly_rate'),('code','name','base_hourly_rate')),
 'qualifications':('routing_qualifications',('code','name','coefficient','sort_order'),('code','name','coefficient','sort_order')),
 'tools':('routing_tools',('code','name','kind'),('code','name','kind')),
}


def dictionary_row(db,resource,row):
    row=number(row)
    if resource=='work-centers':row['department']=obj(db,'routing_departments',row['department_id'])
    if resource in ('professions','operation-types') and row.get('parent_id'):
        row['parent']=obj(db,RESOURCE_FIELDS[resource][0],row['parent_id'])
    return row


def check_dictionary(db,resource,data,current_id=None):
    if resource=='professions' and 'base_hourly_rate' in data:positive(data['base_hourly_rate'],'base_hourly_rate')
    if resource=='qualifications' and 'coefficient' in data:positive(data['coefficient'],'coefficient')
    if resource in ('professions','operation-types') and data.get('parent_id') is not None:
        table=RESOURCE_FIELDS[resource][0]; pid=data['parent_id']
        require(db,table,pid,'Родительский классификатор')
        seen=set([current_id]) if current_id else set()
        while pid is not None:
            if pid in seen:err('CYCLE_DETECTED','Иерархия классификатора не может содержать цикл.',409)
            seen.add(pid);pid=require(db,table,pid)['parent_id']
    if resource=='tools' and 'kind' in data and data['kind'] not in ('tool','fixture'):
        err('INVALID_TOOL_KIND','Инструмент: kind должен быть tool или fixture')


def install_routing(app, get_db):
    """Attach complete routing API to existing Lab 5 FastAPI app."""
    from fastapi import Request
    from fastapi.responses import JSONResponse
    from fastapi.exceptions import RequestValidationError
    from fastapi.exception_handlers import request_validation_exception_handler

    @app.exception_handler(RoutingError)
    async def routing_error_handler(request: Request, exc: RoutingError):
        return JSONResponse(status_code=exc.status,content={'error':{'code':exc.code,'message':exc.message,**(exc.obj or {})}})

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        if not request.url.path.startswith('/api/routing'):
            return await request_validation_exception_handler(request,exc)
        return JSONResponse(status_code=422,content={'error':{'code':'INVALID_REQUEST','message':'Ошибка параметров запроса.',
            'details':jsonable_encoder(exc.errors())}})

    @app.exception_handler(IntegrityError)
    async def integrity_error_handler(request:Request,exc:IntegrityError):
        return JSONResponse(status_code=409,content={'error':{'code':'INTEGRITY_CONFLICT',
               'message':'Ограничение целостности базы данных нарушено.', 'detail':str(exc.orig).split('\n')[0][:250]}})

    @app.exception_handler(DBAPIError)
    async def db_error_handler(request:Request,exc:DBAPIError):
        detail=str(exc.orig).split('\n')[0]
        if ':' in detail:code,message=detail.split(':',1)
        else:code,message='DATABASE_ERROR',detail
        return JSONResponse(status_code=422,content={'error':{'code':code.strip(), 'message':message.strip()}})

    router=APIRouter(prefix='/api/routing',tags=['Lab 6: routing'])

    @router.get('/products')
    def catalog(search:str='',db:Session=Depends(get_db)):
        rows=many(db,'SELECT id_product FROM products WHERE LOWER(name) LIKE LOWER(:term) ORDER BY id_product LIMIT 100',term=f'%{search}%')
        return [product(db,r['id_product']) for r in rows]

    @router.get('/products/{id}/versions')
    def product_versions(id:int,db:Session=Depends(get_db)):
        product(db,id)
        rows=many(db,'SELECT id_spec_version FROM spec_versions WHERE product_id=:id ORDER BY version_num',id=id)
        answer=[]
        for r in rows:
            v=version(db,r['id_spec_version'])
            v['requires_variant']=bool(scalar(db,'''WITH RECURSIVE lineage(vid,base_version_id) AS (
                SELECT id_spec_version,base_version_id FROM spec_versions WHERE id_spec_version=:id
                UNION ALL SELECT sv.id_spec_version,sv.base_version_id
                    FROM spec_versions sv JOIN lineage l ON sv.id_spec_version=l.base_version_id
              ) SELECT 1 FROM spec_config_params cp
                 WHERE cp.spec_version_id IN (SELECT vid FROM lineage) LIMIT 1''',id=v['id']))
            answer.append(v)
        return answer

    @router.get('/spec-versions/{id}/variants')
    def variants_for_version(id:int,db:Session=Depends(get_db)):
        version(db,id)
        rows=many(db,'''SELECT v.id_variant AS id,v.name,v.root_node_id
              FROM spec_variants v JOIN variant_nodes vn ON vn.id_node=v.root_node_id
              WHERE vn.spec_version_id=:id ORDER BY v.id_variant''',id=id)
        result=[]
        for v in rows:
            values=many(db,'''WITH RECURSIVE subtree(id_node) AS (
                  SELECT :root UNION ALL
                  SELECT child.id_node FROM variant_nodes child
                  JOIN subtree s ON child.parent_node_id=s.id_node
                ) SELECT vn.id_node AS node_id,p.code AS parameter_code,p.name AS parameter_name,
                   vv.value_text AS value
                   FROM subtree s JOIN variant_values vv ON vv.node_id=s.id_node
                   JOIN variant_nodes vn ON vn.id_node=vv.node_id
                   JOIN spec_config_params cp ON cp.id_config_param=vv.config_param_id
                   JOIN parameters p ON p.id_parameter=cp.parameter_id
                   ORDER BY vn.id_node,p.code''',root=v['root_node_id'])
            result.append({'id':v['id'],'name':v['name'],'values':values})
        return result

    # Dictionary endpoints registered after concrete ones, no path conflicts.
    def add_dictionary(resource, table, allowed, mandatory):
        # Factory captures internal table names without leaking them as Swagger query parameters.
        def get_all(db: Session=Depends(get_db)):
            return [dictionary_row(db,resource,r) for r in many(db,f'SELECT * FROM {table} ORDER BY id')]
        def get_single(id:int,db:Session=Depends(get_db)):
            return dictionary_row(db,resource,require(db,table,id,'Справочник'))
        def create(data:dict=Body(...,examples=[RESOURCE_EXAMPLES[resource]]),db:Session=Depends(get_db)):
            if any(k not in allowed for k in data):err('UNKNOWN_FIELD','Неизвестное поле справочника.')
            if any(k not in data or data[k] is None for k in mandatory):
                err('REQUIRED_FIELDS',f'Обязательные поля: {", ".join(mandatory)}')
            check_dictionary(db,resource,data)
            new=insert(db,table,data);db.commit()
            return dictionary_row(db,resource,new)
        def patch(id:int,data:dict=Body(...,examples=[{'name':RESOURCE_EXAMPLES[resource]['name']}]),db:Session=Depends(get_db)):
            if any(k not in allowed for k in data):err('UNKNOWN_FIELD','Неизвестное поле справочника.')
            require(db,table,id)
            check_dictionary(db,resource,data,id)
            updated=change(db,table,id,data);db.commit()
            return dictionary_row(db,resource,updated)
        def delete(id:int,db:Session=Depends(get_db)):
            remove(db,table,id);db.commit()
            from fastapi import Response
            return Response(status_code=204)
        router.add_api_route('/'+resource,get_all,methods=['GET'],name='List '+resource)
        router.add_api_route('/'+resource,create,methods=['POST'],status_code=201,name='Create '+resource)
        router.add_api_route('/'+resource+'/{id}',get_single,methods=['GET'],name='Get '+resource)
        router.add_api_route('/'+resource+'/{id}',patch,methods=['PATCH'],name='Update '+resource)
        router.add_api_route('/'+resource+'/{id}',delete,methods=['DELETE'],status_code=204,name='Delete '+resource)

    for resource, (table,allowed,mandatory) in RESOURCE_FIELDS.items():
        add_dictionary(resource,table,allowed,mandatory)

    @router.get('/material-prices')
    def get_material_prices(db:Session=Depends(get_db)):
        rows=many(db,'SELECT product_id,unit_cost FROM routing_material_prices ORDER BY product_id')
        return [{'product':product(db,r['product_id']),
                 'unit':product(db,r['product_id'])['unit']['symbol'],
                 'unit_cost':number(r['unit_cost']),'currency':'RUB'} for r in rows]

    @router.put('/material-prices/{productId}')
    def put_material_price(productId:int,data:dict=Body(...,examples=[{'unit_cost':100}]),db:Session=Depends(get_db)):
        product(db,productId)
        if 'unit_cost' not in data or len(data)!=1:err('REQUIRED_FIELDS','Ожидается только поле unit_cost.')
        value=nonnegative(data['unit_cost'],'unit_cost')
        db.execute(text('''INSERT INTO routing_material_prices(product_id,unit_cost)
                VALUES (:id,:cost) ON CONFLICT(product_id)
                DO UPDATE SET unit_cost=EXCLUDED.unit_cost'''),{'id':productId,'cost':value})
        db.commit()
        p=product(db,productId)
        return {'product':{'id':p['id'],'name':p['name']},'unit':p['unit']['symbol'],
                'unit_cost':number(value),'currency':'RUB'}

    @router.get('/routes')
    def routes(product_id:int|None=None,spec_version_id:int|None=None,variant_id:int|None=None,
               db:Session=Depends(get_db)):
        clauses=[]; params={}
        if spec_version_id is not None:clauses.append('r.spec_version_id=:spec');params['spec']=spec_version_id
        if variant_id is not None:clauses.append('r.variant_id=:variant');params['variant']=variant_id
        if product_id is not None:clauses.append('v.product_id=:product');params['product']=product_id
        sql='''SELECT r.id FROM routing_routes r JOIN spec_versions v ON v.id_spec_version=r.spec_version_id'''
        if clauses:sql+=' WHERE '+' AND '.join(clauses)
        sql+=' ORDER BY r.id'
        return [route_info(db,r['id']) for r in many(db,sql,**params)]

    @router.post('/routes',status_code=201)
    def create_route(data:RouteCreate,db:Session=Depends(get_db)):
        v=version(db,data.spec_version_id)
        if data.variant_id is not None:
            variant_info(db,data.variant_id)
            root_version=scalar(db,'''SELECT vn.spec_version_id FROM spec_variants sv
             JOIN variant_nodes vn ON vn.id_node=sv.root_node_id WHERE sv.id_variant=:id''',id=data.variant_id)
            if root_version!=data.spec_version_id:
                err('VARIANT_MISMATCH','Вариант относится к другой версии спецификации.',422)
        else:
            configured=bool(scalar(db,'''WITH RECURSIVE lineage(vid,base_version_id) AS (
                SELECT id_spec_version,base_version_id FROM spec_versions WHERE id_spec_version=:v
                UNION ALL SELECT sv.id_spec_version,sv.base_version_id
                    FROM spec_versions sv JOIN lineage l ON sv.id_spec_version=l.base_version_id
                ) SELECT 1 FROM spec_config_params c
                WHERE c.spec_version_id IN (SELECT vid FROM lineage) LIMIT 1''',v=data.spec_version_id))
            if configured:err('VARIANT_REQUIRED','Выберите сохранённый вариант конфигурируемого изделия.',422)
        row=insert(db,'routing_routes',data.model_dump())
        db.commit()
        return route_info(db,row['id'])

    @router.get('/routes/{id}')
    def get_route(id:int,db:Session=Depends(get_db)):
        return route_info(db,id)

    @router.patch('/routes/{id}')
    def update_route(id:int,data:RoutePatch,db:Session=Depends(get_db)):
        require_mutable_route(db,id)
        changes={}
        if data.name is not None:changes['name']=data.name
        if 'final_operation_id' in data.model_fields_set:
            target=data.final_operation_id
            if target is not None:
                op=require(db,'routing_nodes',target,'Итоговая операция')
                if op['kind']!='production' or op['route_id']!=id:
                    err('INVALID_FINAL_OPERATION','Итоговая операция должна принадлежать этому маршруту.')
            changes['final_node_id']=target
        change(db,'routing_routes',id,changes)
        db.commit()
        return route_info(db,id)

    @router.delete('/routes/{id}',status_code=204)
    def delete_route(id:int,db:Session=Depends(get_db)):
        r=require_mutable_route(db,id)
        if scalar(db,'SELECT 1 FROM routing_tp_applications WHERE route_id=:id LIMIT 1',id=id):
            err('ROUTE_HAS_APPLICATIONS','Удалите применения типовых процессов перед маршрутом.',409)
        db.execute(text('UPDATE routing_routes SET final_node_id=NULL WHERE id=:id'),{'id':id})
        owned=many(db,"SELECT id FROM routing_nodes WHERE route_id=:id AND kind='production'",id=id)
        ids=[o['id'] for o in owned]
        for oid in ids:
            db.execute(text('DELETE FROM routing_inputs WHERE output_node_id=:id'),{'id':oid})
            db.execute(text('DELETE FROM routing_node_tools WHERE node_id=:id'),{'id':oid})
        for oid in ids:
            if scalar(db,'SELECT 1 FROM routing_inputs WHERE input_node_id=:id LIMIT 1',id=oid):
                err('ROUTE_IN_USE','Результат операции используется в другом маршруте.',409)
        for oid in ids:db.execute(text('DELETE FROM routing_nodes WHERE id=:id'),{'id':oid})
        remove(db,'routing_routes',id)
        db.commit()
        from fastapi import Response
        return Response(status_code=204)

    @router.get('/routes/{id}/operations')
    def list_operations(id:int,db:Session=Depends(get_db)):
        require(db,'routing_routes',id,'Маршрут')
        rows=many(db,"SELECT id FROM routing_nodes WHERE route_id=:id AND kind='production' ORDER BY number",id=id)
        return [operation_info(db,r['id']) for r in rows]

    @router.post('/routes/{id}/operations',status_code=201)
    def create_operation(id:int,data:OperationCreate,db:Session=Depends(get_db)):
        require_mutable_route(db,id)
        nonnegative(data.setup_minutes,'setup_minutes');nonnegative(data.unit_minutes,'unit_minutes')
        d=data.model_dump();d.update({'kind':'production','route_id':id,'operation_name':d.pop('name'),
                                     'state_name':d.pop('result_name')})
        for fld in ('operation_type_id','work_center_id','profession_id','qualification_id'):
            ref={'operation_type_id':'routing_operation_types','work_center_id':'routing_work_centers',
                 'profession_id':'routing_professions','qualification_id':'routing_qualifications'}[fld]
            require(db,ref,d[fld],fld)
        r=insert(db,'routing_nodes',d)
        db.commit()
        return operation_info(db,r['id'])

    @router.get('/operations/{id}')
    def get_operation(id:int,db:Session=Depends(get_db)):
        return operation_info(db,id)

    @router.patch('/operations/{id}')
    def update_operation(id:int,data:OperationPatch,db:Session=Depends(get_db)):
        require_mutable_operation(db,id)
        changes=data.model_dump(exclude_unset=True)
        if 'name' in changes:changes['operation_name']=changes.pop('name')
        if 'result_name' in changes:changes['state_name']=changes.pop('result_name')
        for field in ('unit_minutes','setup_minutes'):
            if field in changes:nonnegative(changes[field],field)
        if changes:change(db,'routing_nodes',id,changes)
        db.commit()
        return operation_info(db,id)

    @router.delete('/operations/{id}',status_code=204)
    def delete_operation(id:int,db:Session=Depends(get_db)):
        r=require_mutable_operation(db,id)
        if scalar(db,'SELECT 1 FROM routing_inputs WHERE input_node_id=:id LIMIT 1',id=id):
            err('OPERATION_IN_USE','Результат операции используется другой операцией.',409)
        rt=require(db,'routing_routes',r['route_id'])
        if rt['final_node_id']==id:
            db.execute(text('UPDATE routing_routes SET final_node_id=NULL WHERE id=:rid'),{'rid':r['route_id']})
        db.execute(text('DELETE FROM routing_inputs WHERE output_node_id=:id'),{'id':id})
        db.execute(text('DELETE FROM routing_node_tools WHERE node_id=:id'),{'id':id})
        remove(db,'routing_nodes',id)
        db.commit()
        from fastapi import Response
        return Response(status_code=204)

    @router.get('/operations/{id}/inputs')
    def list_inputs(id:int,db:Session=Depends(get_db)):
        operation_brief(db,id)
        rows=many(db,'SELECT input_node_id FROM routing_inputs WHERE output_node_id=:id ORDER BY input_node_id',id=id)
        return [input_info(db,id,r['input_node_id']) for r in rows]

    @router.post('/operations/{id}/inputs',status_code=201)
    def create_input(id:int,data:InputCreate,db:Session=Depends(get_db)):
        require_mutable_operation(db,id)
        positive(data.quantity);positive(data.for_quantity,'for_quantity')
        s=data.source
        if s.kind=='purchase' and s.product_id is not None and s.operation_id is None:
            source=buy_node(db,s.product_id)
        elif s.kind=='operation_result' and s.operation_id is not None and s.product_id is None:
            source=s.operation_id
        else:err('INVALID_SOURCE','source должен быть purchase/product_id либо operation_result/operation_id.')
        validate_source(db,id,source)
        check_indivisible_input(db,source,data.quantity,data.for_quantity)
        db.execute(text('''INSERT INTO routing_inputs(output_node_id,input_node_id,quantity,for_quantity)
              VALUES (:op,:src,:q,:fq)'''),{'op':id,'src':source,'q':data.quantity,'fq':data.for_quantity})
        db.commit()
        return input_info(db,id,source)

    @router.patch('/operations/{id}/inputs/{sourceId}')
    def update_input(id:int,sourceId:int,data:InputPatch,db:Session=Depends(get_db)):
        require_mutable_operation(db,id)
        input_info(db,id,sourceId)
        changes=data.model_dump(exclude_unset=True)
        if 'quantity' in changes:positive(changes['quantity'])
        if 'for_quantity' in changes:positive(changes['for_quantity'],'for_quantity')
        if changes:
            previous=one(db,'''SELECT quantity,for_quantity FROM routing_inputs
                      WHERE output_node_id=:id AND input_node_id=:src''',id=id,src=sourceId)
            check_indivisible_input(db,sourceId,
                changes.get('quantity',previous['quantity']),
                changes.get('for_quantity',previous['for_quantity']))
            db.execute(text('UPDATE routing_inputs SET '+','.join(f'{k}=:{k}' for k in changes)
                            +' WHERE output_node_id=:id AND input_node_id=:src'),
                       {**changes,'id':id,'src':sourceId})
        db.commit()
        return input_info(db,id,sourceId)

    @router.delete('/operations/{id}/inputs/{sourceId}',status_code=204)
    def delete_input(id:int,sourceId:int,db:Session=Depends(get_db)):
        require_mutable_operation(db,id)
        input_info(db,id,sourceId)
        db.execute(text('DELETE FROM routing_inputs WHERE output_node_id=:id AND input_node_id=:src'),{'id':id,'src':sourceId})
        db.commit()
        from fastapi import Response
        return Response(status_code=204)

    @router.get('/operations/{id}/tools')
    def list_operation_tools(id:int,db:Session=Depends(get_db)):
        operation_brief(db,id)
        rows=many(db,'SELECT tool_id FROM routing_node_tools WHERE node_id=:id ORDER BY tool_id',id=id)
        return [tool_info(db,id,r['tool_id']) for r in rows]

    @router.post('/operations/{id}/tools',status_code=201)
    def add_operation_tool(id:int,data:ToolLink,db:Session=Depends(get_db)):
        require_mutable_operation(db,id);require(db,'routing_tools',data.tool_id,'Инструмент')
        db.execute(text('INSERT INTO routing_node_tools(node_id,tool_id) VALUES (:n,:t)'),{'n':id,'t':data.tool_id})
        db.commit()
        return tool_info(db,id,data.tool_id)

    @router.delete('/operations/{id}/tools/{toolId}',status_code=204)
    def delete_operation_tool(id:int,toolId:int,db:Session=Depends(get_db)):
        require_mutable_operation(db,id)
        result=db.execute(text('DELETE FROM routing_node_tools WHERE node_id=:n AND tool_id=:t'),{'n':id,'t':toolId})
        if not result.rowcount:err('NOT_FOUND','Связь с инструментом не найдена.',404)
        db.commit()
        from fastapi import Response
        return Response(status_code=204)

    def sql_materials(db,rid,q):
        positive(q)
        require(db,'routing_routes',rid,'Маршрут')
        rows=many(db,'SELECT * FROM "SUM_MR"(:rid,:q)',rid=rid,q=q)
        check_material_units(db,rows)
        return [{'product':{'id':x['product_id'],'name':product(db,x['product_id'])['name']},
                 'product_id':x['product_id'],'quantity':number(x['quantity']),
                 'unit':product(db,x['product_id'])['unit']['symbol']} for x in rows]

    def verify_bom(db,rid,q,materials):
        """Compare route material quantities with the existing Lab5 expansion algorithm."""
        r=require(db,'routing_routes',rid,'Маршрут')
        if r['variant_id'] is None:
            from bom_lab5 import ExpandIn
            endpoint=next((x.endpoint for x in app.routes if getattr(x,'path',None)=='/bom/expand'),None)
            if endpoint is None:err('BOM_UNAVAILABLE','Отсутствует метод раскрытия BOM.')
            result=endpoint(ExpandIn(version_id=r['spec_version_id'],quantity=q),db=db)
        else:
            endpoint=next((x.endpoint for x in app.routes if getattr(x,'path',None)=='/bom/variants/{variant_id}'),None)
            if endpoint is None:err('BOM_UNAVAILABLE','Отсутствует метод чтения варианта BOM.')
            result=endpoint(variant_id=r['variant_id'],category_id=None,db=db)
            # Snapshot is stored for a fixed root quantity. Normalize to requested quantity.
            root_qty=n(result['tree']['quantity'])
            result['leaf_summary']=[{**x,'quantity':n(x['quantity'])*n(q)/root_qty}
                                     for x in result['leaf_summary']]
        expected={x['product_id']:n(x['quantity']) for x in result['leaf_summary']}
        actual={x['product_id']:n(x['quantity']) for x in materials}
        if expected!=actual:
            ex=', '.join(f'{product(db,k)["name"]}: {v}' for k,v in sorted(expected.items()))
            ac=', '.join(f'{product(db,k)["name"]}: {v}' for k,v in sorted(actual.items()))
            err('BOM_MISMATCH',f'Расход по маршруту не совпадает с составом изделия. BOM: [{ex}]. Маршрут: [{ac}].',422)

    def sql_labor(db,rid,q):
        positive(q)
        rows=many(db,'SELECT * FROM "TR_TO"(:rid,:q)',rid=rid,q=q)
        ops=[];by_center=defaultdict(Decimal);by_qualification=defaultdict(Decimal)
        for r in rows:
            minutes=n(r['total_minutes'])
            ops.append({'operation':operation_brief(db,r['operation_id']),
                'work_center':obj(db,'routing_work_centers',r['work_center_id']),
                'profession':obj(db,'routing_professions',r['profession_id']),
                'qualification':obj(db,'routing_qualifications',r['qualification_id']),
                'setup_minutes':number(r['setup_minutes']),'run_minutes':number(r['run_minutes']),
                'total_minutes':number(minutes),'required_quantity':number(r['required_quantity'])})
            by_center[r['work_center_id']]+=minutes
            by_qualification[r['qualification_id']]+=minutes
        return {'route':{'id':rid,'name':require(db,'routing_routes',rid)['name']},
                'operations':ops,'total_minutes':number(sum(by_center.values(),Decimal(0))),
                'by_work_center':[{'work_center':obj(db,'routing_work_centers',k),'minutes':number(v)} for k,v in sorted(by_center.items())],
                'by_qualification':[{'qualification':obj(db,'routing_qualifications',k),'minutes':number(v)} for k,v in sorted(by_qualification.items())]}

    @router.get('/routes/{id}/tree')
    def route_tree(id:int,db:Session=Depends(get_db)):
        r=require(db,'routing_routes',id,'Маршрут')
        # Validate full graph and absence of cycles first, using PostgreSQL.
        if r['final_node_id'] is None:err('INCOMPLETE_ROUTE','У маршрута нет итоговой операции.')
        scalar(db,'SELECT COUNT(*) FROM "FIND_TM"(:rid,1)',rid=id)
        def tree(nid,depth=0):
            if depth>100:err('ROUTE_DEPTH','Слишком большая глубина производственного графа.')
            src=source_info(db,nid)
            result={'source':src}
            if src['kind']=='operation_result':
                result['operation']=operation_info(db,nid)
                tools=many(db,'SELECT tool_id FROM routing_node_tools WHERE node_id=:id',id=nid)
                result['tools']=[obj(db,'routing_tools',t['tool_id']) for t in tools]
                ins=many(db,'SELECT input_node_id,quantity,for_quantity FROM routing_inputs WHERE output_node_id=:id ORDER BY input_node_id',id=nid)
                result['inputs']=[{'norm':{'quantity':number(i['quantity']),'for_quantity':number(i['for_quantity'])},
                                   **tree(i['input_node_id'],depth+1)} for i in ins]
            return result
        return {'route':route_info(db,id),'tree':tree(r['final_node_id'])}

    @router.get('/operations/{id}/materials')
    def operation_materials(id:int,quantity:Decimal=Query(Decimal(1)),db:Session=Depends(get_db)):
        operation_brief(db,id);positive(quantity)
        rows=many(db,'SELECT * FROM "MR_Q"(:id,:q)',id=id,q=quantity)
        for row in rows:
            check_indivisible_source_total(db,row['source_id'],row['quantity'])
        return {'operation':operation_brief(db,id),'quantity':number(quantity),
            'items':[{'source':source_info(db,r['source_id']),
                 'quantity':number(r['quantity']),
                 'unit':source_info(db,r['source_id'])['unit']} for r in rows]}

    @router.get('/routes/{id}/materials')
    def route_materials(id:int,quantity:Decimal=Query(Decimal(1)),db:Session=Depends(get_db)):
        rows=sql_materials(db,id,quantity)
        verify_bom(db,id,quantity,rows)
        return {'route':{'id':id,'name':require(db,'routing_routes',id)['name']},
           'product':route_info_light(db,id)['product'],
           'quantity':number(quantity),'items':rows}

    @router.get('/routes/{id}/labor')
    def route_labor(id:int,quantity:Decimal=Query(Decimal(1)),db:Session=Depends(get_db)):
        return sql_labor(db,id,quantity)

    @router.get('/routes/{id}/cost')
    def route_cost(id:int,quantity:Decimal=Query(Decimal(1)),db:Session=Depends(get_db)):
        rows=sql_materials(db,id,quantity);verify_bom(db,id,quantity,rows)
        summary=scalar(db,'SELECT "ROUTING_COST"(:rid,:q)',rid=id,q=quantity)
        if isinstance(summary,str):
            import json
            summary=json.loads(summary)
        amounts=[]
        for r in rows:
            unit_cost=scalar(db,'SELECT unit_cost FROM routing_material_prices WHERE product_id=:id',id=r['product_id'])
            if unit_cost is None:err('MISSING_MATERIAL_PRICE',f'Нет учетной цены материала «{r["product"]["name"]}».')
            amounts.append({**r,'unit_cost':number(unit_cost),
                'cost':number((n(r['quantity'])*n(unit_cost)).quantize(Decimal('0.01')))})
        labor=sql_labor(db,id,quantity)
        lab=[]
        for op in labor['operations']:
            pid=op['profession']['id'];qid=op['qualification']['id']
            rate=n(require(db,'routing_professions',pid)['base_hourly_rate'])
            coefficient=n(require(db,'routing_qualifications',qid)['coefficient'])
            raw_cost=n(op['total_minutes'])/60*rate*coefficient
            lab.append({**op,'minutes':op['total_minutes'],'base_hourly_rate':number(rate),
                        'qualification_coefficient':number(coefficient),
                        'effective_hourly_rate':number(rate*coefficient),
                        'cost':number(raw_cost.quantize(Decimal('0.01')))})
        return {'route':{'id':id,'name':require(db,'routing_routes',id)['name']},
                'quantity':number(quantity),'currency':'RUB','pricing':'current',
                'materials':amounts,'labor':lab,
                'material_cost':summary['material_cost'],
                'labor_cost':summary['labor_cost'],'total_cost':summary['total_cost']}
    # ---------------- Typical processes / Flyweight ----------------
    def typical_info(db,id):
        r=require(db,'routing_typical_processes',id,'Типовой процесс')
        final=typical_op_info(db,r['final_operation_id']) if r['final_operation_id'] else None
        return {'id':r['id'],'name':r['name'],'final_operation':final}

    def typical_parameter(db,id):
        r=require(db,'routing_tp_parameters',id,'Параметр шаблона')
        return number(r)

    def validate_parameter_value(par,value):
        v=n(value)
        if par.get('min_value') is not None and v<n(par['min_value']):
            err('PARAMETER_OUT_OF_RANGE',f'Значение {par["code"]} меньше минимума {par["min_value"]}.')
        if par.get('max_value') is not None and v>n(par['max_value']):
            err('PARAMETER_OUT_OF_RANGE',f'Значение {par["code"]} больше максимума {par["max_value"]}.')
        if par.get('integer_only',False) and v!=v.to_integral_value():
            err('INTEGER_PARAMETER_REQUIRED',f'Параметр {par["code"]} должен быть целым.')
        return v

    def typical_op_info(db,id):
        r=require(db,'routing_tp_operations',id,'Операция шаблона')
        return number({**r,'name':r['operation_name'],'result_name':r['state_name'],
           'work_center':obj(db,'routing_work_centers',r['work_center_id']),
           'profession':obj(db,'routing_professions',r['profession_id']),
           'qualification':obj(db,'routing_qualifications',r['qualification_id'])})

    def typical_input_info(db,id):
        r=require(db,'routing_tp_inputs',id,'Вход шаблона')
        if r['input_product_id'] is not None:
            source={'kind':'purchase','product':product(db,r['input_product_id'])}
        else:
            op=typical_op_info(db,r['input_operation_id'])
            source={'kind':'operation_result','operation':{'id':op['id'],'name':op['name']}}
        return {**number(r),'source':source}

    def validate_tp_operation(db,process_id,d):
        for field,table in [('operation_type_id','routing_operation_types'),
                            ('work_center_id','routing_work_centers'),
                            ('profession_id','routing_professions'),
                            ('qualification_id','routing_qualifications')]:
            if field in d:require(db,table,d[field],field)
        if 'time_parameter_id' in d and d['time_parameter_id'] is not None:
            p=require(db,'routing_tp_parameters',d['time_parameter_id'],'Параметр времени')
            if p['process_id']!=process_id:err('TEMPLATE_MISMATCH','Параметр времени принадлежит другому шаблону.')
        if 'setup_minutes' in d:nonnegative(d['setup_minutes'],'setup_minutes')

    def validate_tp_input(db,process_id,d,id=None):
        if ('input_product_id' in d) or ('input_operation_id' in d):
            valid=(d.get('input_product_id') is None)!=(d.get('input_operation_id') is None)
            if not valid:err('INVALID_SOURCE','Укажите либо input_product_id, либо input_operation_id.')
        out=require(db,'routing_tp_operations',d['output_operation_id'],'Операция шаблона')
        if out['process_id']!=process_id:err('TEMPLATE_MISMATCH','Выходная операция из другого шаблона.')
        if d.get('input_operation_id') is not None:
            inp=require(db,'routing_tp_operations',d['input_operation_id'],'Входная операция')
            if inp['process_id']!=process_id:err('TEMPLATE_MISMATCH','Входная операция из другого шаблона.')
            if inp['id']==out['id']:err('CYCLE_DETECTED','Операция не может ссылаться на себя.',409)
            edges=many(db,'''SELECT output_operation_id,input_operation_id FROM routing_tp_inputs
                    WHERE input_operation_id IS NOT NULL''')
            graph=defaultdict(list)
            for e in edges:
                if e['output_operation_id']!=out['id'] or e['input_operation_id']!=inp['id']:
                    graph[e['output_operation_id']].append(e['input_operation_id'])
            seen=set();pending=[inp['id']]
            while pending:
                cur=pending.pop()
                if cur==out['id']:err('CYCLE_DETECTED','Цикл типовых операций.',409)
                if cur not in seen:seen.add(cur);pending.extend(graph[cur])
        elif d.get('input_product_id') is not None:product(db,d['input_product_id'])
        if d.get('quantity_parameter_id') is not None:
            par=require(db,'routing_tp_parameters',d['quantity_parameter_id'])
            if par['process_id']!=process_id:err('TEMPLATE_MISMATCH','Параметр расхода из другого шаблона.')
        if 'for_quantity' in d:positive(d['for_quantity'],'for_quantity')

    @router.get('/typical-processes')
    def typical_processes(db:Session=Depends(get_db)):
        return [typical_info(db,r['id']) for r in many(db,'SELECT id FROM routing_typical_processes ORDER BY id')]

    @router.post('/typical-processes',status_code=201)
    def create_typical_process(data:dict=Body(...,examples=[{'name':'Оформление букета'}]),db:Session=Depends(get_db)):
        if not data.get('name') or len(data)!=1:err('INVALID_REQUEST','Ожидается поле name.')
        r=insert(db,'routing_typical_processes',data);db.commit()
        return typical_info(db,r['id'])

    @router.get('/typical-processes/{id}')
    def get_typical_process(id:int,db:Session=Depends(get_db)):
        return typical_info(db,id)

    @router.patch('/typical-processes/{id}')
    def patch_typical_process(id:int,data:dict=Body(...),db:Session=Depends(get_db)):
        require_mutable_template(db,id)
        if set(data)-{'name','final_operation_id'}:err('UNKNOWN_FIELD','Допустимы name, final_operation_id.')
        if 'final_operation_id' in data:
            op=data['final_operation_id']
            if op is not None and require(db,'routing_tp_operations',op)['process_id']!=id:
                err('INVALID_FINAL_OPERATION','Итоговая операция из другого шаблона.')
        if data:
            mapped={('final_operation_id' if k=='final_operation_id' else k):v for k,v in data.items()}
            change(db,'routing_typical_processes',id,mapped)
        db.commit()
        return typical_info(db,id)

    @router.delete('/typical-processes/{id}',status_code=204)
    def delete_typical_process(id:int,db:Session=Depends(get_db)):
        require_mutable_template(db,id)
        db.execute(text('UPDATE routing_typical_processes SET final_operation_id=NULL WHERE id=:id'),{'id':id})
        ops=many(db,'SELECT id FROM routing_tp_operations WHERE process_id=:id',id=id)
        for op in ops:
            db.execute(text('DELETE FROM routing_tp_inputs WHERE output_operation_id=:oid OR input_operation_id=:oid'),{'oid':op['id']})
            db.execute(text('DELETE FROM routing_tp_tools WHERE operation_id=:oid'),{'oid':op['id']})
        db.execute(text('DELETE FROM routing_tp_operations WHERE process_id=:id'),{'id':id})
        db.execute(text('DELETE FROM routing_tp_parameters WHERE process_id=:id'),{'id':id})
        remove(db,'routing_typical_processes',id);db.commit()
        from fastapi import Response
        return Response(status_code=204)

    @router.get('/typical-processes/{id}/parameters')
    def list_typical_parameters(id:int,db:Session=Depends(get_db)):
        require(db,'routing_typical_processes',id)
        return [number(r) for r in many(db,'SELECT * FROM routing_tp_parameters WHERE process_id=:id ORDER BY id',id=id)]

    @router.post('/typical-processes/{id}/parameters',status_code=201)
    def create_typical_parameter(id:int,data:dict=Body(...,examples=[{
        'code':'size','name':'Размер оформления','default_value':1,
        'min_value':1,'max_value':2,'integer_only':True}]),db:Session=Depends(get_db)):
        require_mutable_template(db,id)
        mandatory=['code','name','default_value']
        if any(k not in data for k in mandatory):err('REQUIRED_FIELDS',', '.join(mandatory))
        if set(data)-{'code','name','default_value','min_value','max_value','integer_only'}:err('UNKNOWN_FIELD','Неизвестное поле параметра.')
        values={'process_id':id,'integer_only':False,**data}
        validate_parameter_value(values,values['default_value'])
        row=insert(db,'routing_tp_parameters',values);db.commit()
        return number(row)

    @router.patch('/typical-parameters/{id}')
    def patch_typical_parameter(id:int,data:dict=Body(...),db:Session=Depends(get_db)):
        par=require(db,'routing_tp_parameters',id,'Параметр')
        require_mutable_template(db,par['process_id'])
        if set(data)-{'code','name','default_value','min_value','max_value','integer_only'}:err('UNKNOWN_FIELD','Неизвестное поле параметра.')
        vals={**par,**data};validate_parameter_value(vals,vals['default_value'])
        if data:change(db,'routing_tp_parameters',id,data)
        db.commit()
        return typical_parameter(db,id)

    @router.delete('/typical-parameters/{id}',status_code=204)
    def delete_typical_parameter(id:int,db:Session=Depends(get_db)):
        par=require(db,'routing_tp_parameters',id,'Параметр')
        require_mutable_template(db,par['process_id'])
        remove(db,'routing_tp_parameters',id);db.commit()
        from fastapi import Response
        return Response(status_code=204)

    @router.get('/typical-processes/{id}/operations')
    def list_typical_operations(id:int,db:Session=Depends(get_db)):
        require(db,'routing_typical_processes',id)
        return [typical_op_info(db,r['id']) for r in many(db,'SELECT id FROM routing_tp_operations WHERE process_id=:id ORDER BY number',id=id)]

    @router.post('/typical-processes/{id}/operations',status_code=201)
    def create_typical_operation(id:int,data:dict=Body(...,examples=[{
        'number':10,'operation_name':'Упаковать букет','state_name':'Упакованный букет',
        'operation_type_id':3,'work_center_id':3,'profession_id':1,'qualification_id':1,
        'setup_minutes':2,'time_base':1,'time_factor':2,'time_parameter_id':1}]),db:Session=Depends(get_db)):
        require_mutable_template(db,id)
        mandatory={'number','operation_name','state_name','operation_type_id',
                   'work_center_id','profession_id','qualification_id','setup_minutes','time_base','time_factor'}
        if mandatory-set(data):err('REQUIRED_FIELDS',', '.join(sorted(mandatory-set(data))))
        allowed=mandatory|{'time_parameter_id','description'}
        if set(data)-allowed:err('UNKNOWN_FIELD','Неизвестное поле операции типового процесса.')
        validate_tp_operation(db,id,data)
        row=insert(db,'routing_tp_operations',{'process_id':id,**data})
        db.commit()
        return typical_op_info(db,row['id'])

    @router.get('/typical-operations/{id}')
    def get_typical_operation(id:int,db:Session=Depends(get_db)):
        return typical_op_info(db,id)

    @router.patch('/typical-operations/{id}')
    def patch_typical_operation(id:int,data:dict=Body(...),db:Session=Depends(get_db)):
        op=require(db,'routing_tp_operations',id)
        require_mutable_template(db,op['process_id'])
        allowed={'number','operation_name','state_name','description','operation_type_id','work_center_id',
                 'profession_id','qualification_id','setup_minutes','time_base','time_factor','time_parameter_id'}
        if set(data)-allowed:err('UNKNOWN_FIELD','Неизвестное поле типовой операции.')
        validate_tp_operation(db,op['process_id'],data)
        if data:change(db,'routing_tp_operations',id,data)
        db.commit()
        return typical_op_info(db,id)

    @router.delete('/typical-operations/{id}',status_code=204)
    def delete_typical_operation(id:int,db:Session=Depends(get_db)):
        op=require(db,'routing_tp_operations',id)
        require_mutable_template(db,op['process_id'])
        if scalar(db,'SELECT 1 FROM routing_tp_inputs WHERE input_operation_id=:id LIMIT 1',id=id):
            err('OPERATION_IN_USE','Типовая операция используется как вход другой операции.',409)
        tpl=require(db,'routing_typical_processes',op['process_id'])
        if tpl['final_operation_id']==id:
            db.execute(text('UPDATE routing_typical_processes SET final_operation_id=NULL WHERE id=:id'),{'id':op['process_id']})
        db.execute(text('DELETE FROM routing_tp_inputs WHERE output_operation_id=:id'),{'id':id})
        db.execute(text('DELETE FROM routing_tp_tools WHERE operation_id=:id'),{'id':id})
        remove(db,'routing_tp_operations',id);db.commit()
        from fastapi import Response
        return Response(status_code=204)

    @router.get('/typical-operations/{id}/inputs')
    def list_typical_inputs(id:int,db:Session=Depends(get_db)):
        require(db,'routing_tp_operations',id)
        return [typical_input_info(db,r['id']) for r in many(db,'SELECT id FROM routing_tp_inputs WHERE output_operation_id=:id ORDER BY id',id=id)]

    @router.post('/typical-operations/{id}/inputs',status_code=201)
    def create_typical_input(id:int,data:dict=Body(...,examples=[{
        'input_operation_id':None,'input_product_id':12,'quantity_base':0,
        'quantity_factor':1,'quantity_parameter_id':1,'for_quantity':1}]),db:Session=Depends(get_db)):
        op=require(db,'routing_tp_operations',id,'Операция шаблона')
        require_mutable_template(db,op['process_id'])
        allowed={'output_operation_id','input_operation_id','input_product_id','quantity_base','quantity_factor','quantity_parameter_id','for_quantity'}
        if set(data)-allowed:err('UNKNOWN_FIELD','Неизвестное поле нормы шаблона.')
        if data.get('output_operation_id',id)!=id:err('TEMPLATE_MISMATCH','output_operation_id не совпадает с ID в маршруте запроса.')
        full={'output_operation_id':id,'quantity_base':0,'quantity_factor':0,'for_quantity':1,**data}
        validate_tp_input(db,op['process_id'],full)
        r=insert(db,'routing_tp_inputs',full);db.commit()
        return typical_input_info(db,r['id'])

    @router.patch('/typical-inputs/{id}')
    def patch_typical_input(id:int,data:dict=Body(...),db:Session=Depends(get_db)):
        inp=require(db,'routing_tp_inputs',id)
        op=require(db,'routing_tp_operations',inp['output_operation_id'])
        require_mutable_template(db,op['process_id'])
        allowed={'input_operation_id','input_product_id','quantity_base','quantity_factor','quantity_parameter_id','for_quantity'}
        if set(data)-allowed:err('UNKNOWN_FIELD','Неизвестное поле нормы.')
        full={**inp,**data};validate_tp_input(db,op['process_id'],full,id)
        if data:change(db,'routing_tp_inputs',id,data)
        db.commit()
        return typical_input_info(db,id)

    @router.delete('/typical-inputs/{id}',status_code=204)
    def delete_typical_input(id:int,db:Session=Depends(get_db)):
        inp=require(db,'routing_tp_inputs',id)
        op=require(db,'routing_tp_operations',inp['output_operation_id'])
        require_mutable_template(db,op['process_id'])
        remove(db,'routing_tp_inputs',id);db.commit()
        from fastapi import Response
        return Response(status_code=204)

    @router.get('/typical-operations/{id}/tools')
    def list_typical_tools(id:int,db:Session=Depends(get_db)):
        require(db,'routing_tp_operations',id)
        return [{'operation':typical_op_info(db,id),'tool':obj(db,'routing_tools',r['tool_id'])} for r in
                many(db,'SELECT tool_id FROM routing_tp_tools WHERE operation_id=:id',id=id)]

    @router.post('/typical-operations/{id}/tools',status_code=201)
    def create_typical_tool(id:int,data:ToolLink,db:Session=Depends(get_db)):
        op=require(db,'routing_tp_operations',id);require_mutable_template(db,op['process_id'])
        require(db,'routing_tools',data.tool_id)
        db.execute(text('INSERT INTO routing_tp_tools(operation_id,tool_id) VALUES (:op,:tool)'),
                   {'op':id,'tool':data.tool_id})
        db.commit()
        return {'operation':typical_op_info(db,id),'tool':obj(db,'routing_tools',data.tool_id)}

    @router.delete('/typical-operations/{id}/tools/{toolId}',status_code=204)
    def delete_typical_tool(id:int,toolId:int,db:Session=Depends(get_db)):
        op=require(db,'routing_tp_operations',id);require_mutable_template(db,op['process_id'])
        nrows=db.execute(text('DELETE FROM routing_tp_tools WHERE operation_id=:id AND tool_id=:tool'),
                         {'id':id,'tool':toolId}).rowcount
        if not nrows:err('NOT_FOUND','Связь типового инструмента не найдена.',404)
        db.commit()
        from fastapi import Response
        return Response(status_code=204)

    def parameter_values(db,process_id,parameters):
        pars=many(db,'SELECT * FROM routing_tp_parameters WHERE process_id=:id',id=process_id)
        recognized={p['code'] for p in pars}
        extra=set(parameters)-recognized
        if extra:err('UNKNOWN_PARAMETER',f'Неизвестные параметры: {", ".join(sorted(extra))}')
        vals={}
        for p in pars:
            v=parameters.get(p['code'],p['default_value'])
            if v is None:err('REQUIRED_PARAMETER',f'Отсутствует параметр «{p["code"]}».')
            vals[p['id']]=validate_parameter_value(p,v)
        return vals

    def calc_expr(db,base,factor,parameter_id,values,name):
        if parameter_id is None:
            if n(factor)!=0:err('INVALID_PARAMETER_EXPRESSION',f'{name}: коэффициент без параметра должен быть 0.')
            value=n(base)
        else:
            if parameter_id not in values:err('UNKNOWN_PARAMETER',f'{name}: параметр отсутствует.')
            value=n(base)+n(factor)*values[parameter_id]
        return value

    def apply_materialized_values(db,app_id,process_id,route_id,values,create=False):
        ops=many(db,'SELECT * FROM routing_tp_operations WHERE process_id=:id ORDER BY number',id=process_id)
        process=require(db,'routing_typical_processes',process_id)
        if not ops or process['final_operation_id'] is None:
            err('INCOMPLETE_TEMPLATE','Шаблон не содержит итоговой операции или операций.')
        existing=many(db,'SELECT id,template_operation_id FROM routing_nodes WHERE application_id=:id',id=app_id)
        op_to_node={r['template_operation_id']:r['id'] for r in existing}
        # All generated nodes are recognized only by stable application/template references.
        for op in ops:
            unit=calc_expr(db,op['time_base'],op['time_factor'],op['time_parameter_id'],values,'Время')
            nonnegative(unit,'unit_minutes')
            if create:
                node=insert(db,'routing_nodes',{
                    'kind':'production','route_id':route_id,'number':op['number'],
                    'operation_name':op['operation_name'],'state_name':op['state_name'],
                    'description':op['description'],'operation_type_id':op['operation_type_id'],
                    'work_center_id':op['work_center_id'],'profession_id':op['profession_id'],
                    'qualification_id':op['qualification_id'],
                    'setup_minutes':op['setup_minutes'],'unit_minutes':unit,
                    'application_id':app_id,'template_operation_id':op['id']})
                op_to_node[op['id']]=node['id']
                for t in many(db,'SELECT tool_id FROM routing_tp_tools WHERE operation_id=:op',op=op['id']):
                    db.execute(text('INSERT INTO routing_node_tools(node_id,tool_id) VALUES (:id,:t)'),
                               {'id':node['id'],'t':t['tool_id']})
            else:
                nid=op_to_node.get(op['id'])
                if not nid:err('APPLICATION_CORRUPTED','Не найдена вычисляемая операция применения.')
                db.execute(text('UPDATE routing_nodes SET unit_minutes=:u WHERE id=:id'),{'u':unit,'id':nid})
        # Delete generated edges before re-installing on PATCH.
        for node_id in op_to_node.values():
            db.execute(text('DELETE FROM routing_inputs WHERE output_node_id=:id'),{'id':node_id})
        for templ in many(db,'''SELECT i.* FROM routing_tp_inputs i
             JOIN routing_tp_operations op ON op.id=i.output_operation_id
             WHERE op.process_id=:id ORDER BY i.id''',id=process_id):
            qty=calc_expr(db,templ['quantity_base'],templ['quantity_factor'],templ['quantity_parameter_id'],values,'Норма расхода')
            positive(qty)
            target=op_to_node[templ['output_operation_id']]
            if templ['input_operation_id'] is not None:source=op_to_node[templ['input_operation_id']]
            else:source=buy_node(db,templ['input_product_id'])
            check_indivisible_input(db,source,qty,templ['for_quantity'])
            check_no_cycle(db,target,source)
            db.execute(text('INSERT INTO routing_inputs(output_node_id,input_node_id,quantity,for_quantity) VALUES (:o,:i,:q,:fq)'),
                       {'o':target,'i':source,'q':qty,'fq':templ['for_quantity']})
        final_node=op_to_node[process['final_operation_id']]
        if create:
            db.execute(text('INSERT INTO routing_tp_application_outputs(application_id,node_id) VALUES (:a,:n)'),
                       {'a':app_id,'n':final_node})
        return final_node

    def app_info(db,id):
        appl=require(db,'routing_tp_applications',id,'Применение типового процесса')
        pars=many(db,'''SELECT p.code,p.name,v.value FROM routing_tp_values v
            JOIN routing_tp_parameters p ON p.id=v.parameter_id WHERE v.application_id=:id ORDER BY p.id''',id=id)
        ops=many(db,'SELECT id FROM routing_nodes WHERE application_id=:id ORDER BY number',id=id)
        final=one(db,'SELECT node_id FROM routing_tp_application_outputs WHERE application_id=:id',id=id)
        materials=[]
        if final:
            # The application's own final output may not yet be a route final.
            # Traverse its graph locally and include all leaf purchases.
            quantities=defaultdict(Decimal)
            def walk(nid,q,path):
                if nid in path:err('CYCLE_DETECTED','Цикл операций приложения.',409)
                item=require(db,'routing_nodes',nid)
                if item['kind']=='purchase':
                    quantities[item['purchase_product_id']]+=q;return
                inputs=many(db,'SELECT * FROM routing_inputs WHERE output_node_id=:id',id=nid)
                for e in inputs:
                    walk(e['input_node_id'],q*n(e['quantity'])/n(e['for_quantity']),path|{nid})
            walk(final['node_id'],Decimal(1),set())
            check_material_units(db,[{'product_id':pid,'quantity':q} for pid,q in quantities.items()])
            materials=[{'product':{'id':pid,'name':product(db,pid)['name']},
                        'quantity':number(q),'unit':product(db,pid)['unit']['symbol']}
                       for pid,q in sorted(quantities.items())]
        return {'id':id,'route':route_info_light(db,appl['route_id']),
                'process':{'id':appl['process_id'],'name':require(db,'routing_typical_processes',appl['process_id'])['name']},
                'parameters':[{**p,'value':number(p['value'])} for p in pars],
                'operations':[operation_info(db,r['id']) for r in ops],
                'final_operation':operation_brief(db,final['node_id']) if final else None,
                'materials':materials}

    @router.get('/routes/{id}/typical-applications')
    def list_applications(id:int,db:Session=Depends(get_db)):
        require(db,'routing_routes',id)
        return [app_info(db,r['id']) for r in many(db,'SELECT id FROM routing_tp_applications WHERE route_id=:id',id=id)]

    @router.post('/routes/{id}/typical-applications',status_code=201)
    def create_application(id:int,data:dict=Body(...,examples=[{'process_id':1,'parameters':{'size':1}}]),db:Session=Depends(get_db)):
        require_mutable_route(db,id)
        if set(data)-{'process_id','parameters'} or 'process_id' not in data:
            err('INVALID_REQUEST','Ожидаются process_id и parameters.')
        process=require(db,'routing_typical_processes',data['process_id'],'Типовой процесс')
        values=parameter_values(db,process['id'],data.get('parameters',{}))
        app_row=insert(db,'routing_tp_applications',{'route_id':id,'process_id':process['id']})
        for parameter_id,val in values.items():
            db.execute(text('INSERT INTO routing_tp_values(application_id,parameter_id,value) VALUES (:a,:p,:v)'),
                       {'a':app_row['id'],'p':parameter_id,'v':val})
        apply_materialized_values(db,app_row['id'],process['id'],id,values,create=True)
        db.commit()
        return app_info(db,app_row['id'])

    @router.get('/typical-applications/{id}')
    def get_application(id:int,db:Session=Depends(get_db)):
        return app_info(db,id)

    @router.patch('/typical-applications/{id}')
    def patch_application(id:int,data:dict=Body(...,examples=[{'parameters':{'size':2}}]),db:Session=Depends(get_db)):
        app_row=require(db,'routing_tp_applications',id,'Применение')
        require_mutable_route(db,app_row['route_id'])
        if set(data)!={'parameters'} or not isinstance(data['parameters'],dict):
            err('INVALID_REQUEST','Ожидается {"parameters":{"code":value}}.')
        current=many(db,'''SELECT p.code,v.value FROM routing_tp_values v
                    JOIN routing_tp_parameters p ON p.id=v.parameter_id WHERE v.application_id=:id''',id=id)
        combined={p['code']:p['value'] for p in current};combined.update(data['parameters'])
        values=parameter_values(db,app_row['process_id'],combined)
        for pid,value in values.items():
            db.execute(text('UPDATE routing_tp_values SET value=:v WHERE application_id=:a AND parameter_id=:p'),
                       {'v':value,'a':id,'p':pid})
        apply_materialized_values(db,id,app_row['process_id'],app_row['route_id'],values)
        db.commit()
        return app_info(db,id)

    @router.delete('/typical-applications/{id}',status_code=204)
    def delete_application(id:int,db:Session=Depends(get_db)):
        app_row=require(db,'routing_tp_applications',id,'Применение')
        require_mutable_route(db,app_row['route_id'])
        owned=many(db,'SELECT id FROM routing_nodes WHERE application_id=:id',id=id)
        ids=[r['id'] for r in owned]
        for nid in ids:
            db.execute(text('DELETE FROM routing_inputs WHERE output_node_id=:id'),{'id':nid})
            db.execute(text('DELETE FROM routing_node_tools WHERE node_id=:id'),{'id':nid})
        for nid in ids:
            if scalar(db,'SELECT 1 FROM routing_inputs WHERE input_node_id=:id LIMIT 1',id=nid):
                err('OPERATION_IN_USE','Результат операции применения используется.',409)
        root=require(db,'routing_routes',app_row['route_id'])
        if root['final_node_id'] in ids:
            db.execute(text('UPDATE routing_routes SET final_node_id=NULL WHERE id=:id'),{'id':app_row['route_id']})
        db.execute(text('DELETE FROM routing_tp_application_outputs WHERE application_id=:id'),{'id':id})
        for nid in ids:db.execute(text('DELETE FROM routing_nodes WHERE id=:id'),{'id':nid})
        db.execute(text('DELETE FROM routing_tp_values WHERE application_id=:id'),{'id':id})
        remove(db,'routing_tp_applications',id)
        db.commit()
        from fastapi import Response
        return Response(status_code=204)

    app.include_router(router)
    return router
