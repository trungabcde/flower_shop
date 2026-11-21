"""API-only example data for Lab 6, using real IDs returned by Swagger endpoints.

Prerequisite: start the Lab 6 server and run the Lab 6 migration/functions.
Idempotent for named products, route/operation records, reference catalog and templates.
Does not drop or overwrite existing Lab 5 products/BOM versions.
"""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request,urlopen

LAB5=Path(__file__).resolve().parent.parent/'lab5'
if str(LAB5) not in sys.path:sys.path.insert(0,str(LAB5))
from demo_lab5 import unit,category,product,first_version  # uses FLOWER_SHOP_API

BASE=os.getenv('FLOWER_SHOP_API','http://127.0.0.1:8001').rstrip('/')

def call(method,path,data=None,params=None):
    url=BASE+path+(('?' + urlencode(params)) if params else '')
    payload=json.dumps(data,ensure_ascii=False).encode('utf-8') if data is not None else None
    req=Request(url,data=payload,method=method,
                headers={'Content-Type':'application/json'} if payload else {})
    try:
        with urlopen(req,timeout=30) as resp:
            body=resp.read().decode('utf8')
            return json.loads(body) if body else None
    except HTTPError as e:
        raise RuntimeError(f'{method} {url}: HTTP {e.code}\n{e.read().decode("utf8")}') from e

def get(path,params=None):return call('GET',path,params=params)
def post(path,data):return call('POST',path,data)
def patch(path,data):return call('PATCH',path,data)
def put(path,data):return call('PUT',path,data)

def ref(resource,code,**body):
    uri='/api/routing/'+resource
    for entry in get(uri):
        if entry['code']==code:
            return entry['id']
    return post(uri,{'code':code,**body})['id']

def operation(route_id,number,**fields):
    for op in get(f'/api/routing/routes/{route_id}/operations'):
        if op['number']==number:return op['id']
    return post(f'/api/routing/routes/{route_id}/operations',{'number':number,**fields})['id']

def route(vid,name):
    existing=get('/api/routing/routes',{'spec_version_id':vid})
    if existing:return existing[0]['id']
    return post('/api/routing/routes',{'name':name,'spec_version_id':vid,'variant_id':None})['id']

def has_input(operation_id,kind,source_id):
    for inp in get(f'/api/routing/operations/{operation_id}/inputs'):
        src=inp['source']
        if src['kind']==kind:
            if kind=='purchase' and src['product']['id']==source_id:return True
            if kind=='operation_result' and src['operation']['id']==source_id:return True
    return False

def inp(op,kind,source_id,quantity,for_quantity=1):
    if has_input(op,kind,source_id):return
    kind_object=({'kind':'purchase','product_id':source_id} if kind=='purchase'
                 else {'kind':'operation_result','operation_id':source_id})
    post(f'/api/routing/operations/{op}/inputs',
         {'source':kind_object,'quantity':quantity,'for_quantity':for_quantity})

def tool(op,tid):
    if any(row['tool']['id']==tid for row in get(f'/api/routing/operations/{op}/tools')):return
    post(f'/api/routing/operations/{op}/tools',{'tool_id':tid})

def finish(rid,op):
    r=get(f'/api/routing/routes/{rid}')
    if r['final_operation'] is None or r['final_operation']['id']!=op:
        patch(f'/api/routing/routes/{rid}',{'final_operation_id':op})

def check(rid,quantity,mat,labor,total):
    result=get(f'/api/routing/routes/{rid}/cost',{'quantity':quantity})
    vals=(result['material_cost'],result['labor_cost'],result['total_cost'])
    print('Route',rid,'quantity',quantity,'costs',vals)
    assert tuple(round(float(v),2) for v in vals)==(mat,labor,total), f'Unexpected costs: {vals}'


def main():
    print('Demo API:',BASE)
    piece=unit('шт.','штука',True)
    sheet=unit('лист','лист',True)
    meter=unit('м','метр',False)
    category_id=category('LAB6_DEMO','Учебные изделия ЛР6',piece)
    flower=category('LAB6_FLOWER','Материалы флористики ЛР6',piece,category_id)
    material=category('LAB6_PACK','Упаковка ЛР6',piece,category_id)
    assembly=category('LAB6_ASSEMBLY','Букеты ЛР6',piece,category_id)
    rose=product('LAB6_ROSE','Роза учебная',flower,piece,100)
    wrap=product('LAB6_WRAP','Упаковка учебная',material,sheet,20)
    ribbon=product('LAB6_RIBBON','Лента учебная',material,meter,10)
    card=product('LAB6_CARD','Открытка учебная',material,piece,30)
    bouquet=product('LAB6_BOUQUET','Учебный букет',assembly,piece,500)
    small=product('LAB6_WRAP_SMALL','Малое оформление',assembly,piece,100)
    large=product('LAB6_WRAP_LARGE','Большое оформление',assembly,piece,200)
    gift=product('LAB6_GIFT','Набор с двумя букетами',assembly,piece,1200)
    ver_bouquet=first_version(bouquet,[{'component_product_id':rose,'quantity':5},
                                       {'component_product_id':wrap,'quantity':1},
                                       {'component_product_id':ribbon,'quantity':2}])
    ver_small=first_version(small,[{'component_product_id':wrap,'quantity':1},
                                   {'component_product_id':ribbon,'quantity':2}])
    ver_large=first_version(large,[{'component_product_id':wrap,'quantity':2},
                                   {'component_product_id':ribbon,'quantity':4}])
    ver_gift=first_version(gift,[{'component_version_id':ver_bouquet,'quantity':2},
                                 {'component_product_id':card,'quantity':1}])

    department=ref('departments','workshop',name='Флористическая мастерская')
    center={c:ref('work-centers',c,name=n,type_name='Рабочее место флориста',department_id=department)
            for c,n in [('preparation','Станция подготовки цветов'),('assembly','Станция сборки'),
                        ('packaging','Станция упаковки')]}
    op_type={c:ref('operation-types',c,name=n,parent_id=None)
             for c,n in [('preparation','Подготовка'),('assembly','Сборка'),
                         ('packaging','Упаковка'),('completion','Комплектация')]}
    florist=ref('professions','florist',name='Флорист',parent_id=None,base_hourly_rate=300)
    quals={c:ref('qualifications',c,name=name,coefficient=coeff,sort_order=i)
           for i,(c,name,coeff) in enumerate([
                ('simple','Работа с простыми изделиями',1),
                ('medium','Работа с изделиями средней сложности',1.5),
                ('complex','Работа со сложными изделиями',2)],1)}
    tools={c:ref('tools',c,name=name,kind='tool') for c,name in
           [('secateur','Секатор'),('florist_knife','Флористический нож'),('scissors','Ножницы')]}
    # Ensure the normative reference costs are deterministic even on a reused DB.
    patch(f'/api/routing/professions/{florist}',{'base_hourly_rate':300})
    for code,coef in [('simple',1),('medium',1.5),('complex',2)]:
        patch(f'/api/routing/qualifications/{quals[code]}',{'coefficient':coef})
    for pid,price in [(rose,100),(wrap,20),(ribbon,10),(card,30)]:
        put(f'/api/routing/material-prices/{pid}',{'unit_cost':price})
    print('Reference IDs:',center,op_type,florist,quals,tools)

    r1=route(ver_bouquet,'Изготовление учебного букета')
    def operation_options(label,result,kind,station,setup,run):
        return {'name':label,'result_name':result,
                'operation_type_id':op_type[kind],'work_center_id':center[station],
                'profession_id':florist,'qualification_id':quals['simple'],
                'setup_minutes':setup,'unit_minutes':run}
    prep=operation(r1,10,**operation_options('Подготовить цветы','Подготовленные цветы для букета',
                                         'preparation','preparation',2,2))
    assembl=operation(r1,20,**operation_options('Собрать букет','Собранный букет',
                                         'assembly','assembly',5,10))
    pack=operation(r1,30,**operation_options('Упаковать букет','Упакованный букет',
                                         'packaging','packaging',2,3))
    inp(prep,'purchase',rose,5)
    inp(assembl,'operation_result',prep,1)
    inp(pack,'operation_result',assembl,1)
    inp(pack,'purchase',wrap,1)
    inp(pack,'purchase',ribbon,2)
    tool(prep,tools['secateur']);tool(pack,tools['scissors'])
    finish(r1,pack)
    check(r1,2,1080,195,1275)
    print('LAB6 demo bouquet:',r1,'operation IDs:',prep,assembl,pack)

    # Nested route: 2 completed bouquets plus an additional greeting card.
    r2=route(ver_gift,'Набор с двумя учебными букетами')
    finish_gift=operation(r2,10,**operation_options('Комплектовать набор','Готовый подарочный набор',
                                         'completion','assembly',1,4))
    inp(finish_gift,'operation_result',pack,2)
    inp(finish_gift,'purchase',card,1)
    finish(r2,finish_gift)
    check(r2,1,1110,220,1330)

    # Flyweight template: a single immutable process used with size=1 and size=2.
    name='Оформление букета'
    existing=next((t for t in get('/api/routing/typical-processes') if t['name']==name),None)
    tid=existing['id'] if existing else post('/api/routing/typical-processes',{'name':name})['id']
    pars=get(f'/api/routing/typical-processes/{tid}/parameters')
    size=next((p['id'] for p in pars if p['code']=='size'),None)
    if size is None:
        size=post(f'/api/routing/typical-processes/{tid}/parameters',
            {'code':'size','name':'Размер оформления','default_value':1,
             'min_value':1,'max_value':2,'integer_only':True})['id']
    tops=get(f'/api/routing/typical-processes/{tid}/operations')
    top=next((p['id'] for p in tops if p['number']==10),None)
    if top is None:
        top=post(f'/api/routing/typical-processes/{tid}/operations',{
            'number':10,'operation_name':'Упаковать букет','state_name':'Упакованный букет',
            'operation_type_id':op_type['packaging'],'work_center_id':center['packaging'],
            'profession_id':florist,'qualification_id':quals['simple'],
            'setup_minutes':2,'time_base':1,'time_factor':2,'time_parameter_id':size})['id']
    entries=get(f'/api/routing/typical-operations/{top}/inputs')
    for pid,factor in [(wrap,1),(ribbon,2)]:
        if not any(i['input_product_id']==pid for i in entries):
            post(f'/api/routing/typical-operations/{top}/inputs',{
                'input_operation_id':None,'input_product_id':pid,'quantity_base':0,
                'quantity_factor':factor,'quantity_parameter_id':size,'for_quantity':1})
    if not any(x['tool']['id']==tools['scissors'] for x in get(f'/api/routing/typical-operations/{top}/tools')):
        post(f'/api/routing/typical-operations/{top}/tools',{'tool_id':tools['scissors']})
    if get(f'/api/routing/typical-processes/{tid}')['final_operation'] is None:
        patch(f'/api/routing/typical-processes/{tid}',{'final_operation_id':top})
    for vid,label,sz,mat_cost,labor_cost,total in [
            (ver_small,'Малое оформление',1,40,25,65),
            (ver_large,'Большое оформление',2,80,35,115)]:
        rid=route(vid,label)
        apps=get(f'/api/routing/routes/{rid}/typical-applications')
        app_id=apps[0]['id'] if apps else post(f'/api/routing/routes/{rid}/typical-applications',
                           {'process_id':tid,'parameters':{'size':sz}})['id']
        final=get(f'/api/routing/typical-applications/{app_id}')['final_operation']['id']
        finish(rid,final)
        check(rid,1,mat_cost,labor_cost,total)
    # Additional scenarios requested by ТЗ: different materials and complexity levels.
    # One-stage valid routes are sufficient for contrasting materials, units and rates.
    extra_materials=[
        ('LAB6_GREEN','Зелень для оформления',flower,piece,25),
        ('LAB6_DAISY','Ромашка',flower,piece,60),
        ('LAB6_SPONGE','Флористическая губка',material,piece,90),
        ('LAB6_BASKET_RESOURCE','Корзина плетёная',material,piece,150),
        ('LAB6_DRY','Сухоцветы',flower,piece,80),
        ('LAB6_PLANT_RESOURCE','Растение в горшке',flower,piece,180),
        ('LAB6_POT_RESOURCE','Декоративный горшок',material,piece,120),
    ]
    extra={}
    for code,name,cat,ut,price in extra_materials:
        extra[code]=product(code,name,cat,ut,price)
        put(f'/api/routing/material-prices/{extra[code]}',{'unit_cost':price})
    examples=[
        ('LAB6_MIXED','Смешанный букет','medium',
         [(rose,3),(extra['LAB6_DAISY'],2),(extra['LAB6_GREEN'],2),(wrap,1),(ribbon,2)]),
        ('LAB6_BASKET_ARRANGEMENT','Корзина с флористической губкой','medium',
         [(rose,2),(extra['LAB6_GREEN'],3),(extra['LAB6_SPONGE'],1),(extra['LAB6_BASKET_RESOURCE'],1)]),
        ('LAB6_DRY_BOUQUET','Букет из сухоцветов','simple',
         [(extra['LAB6_DRY'],7),(wrap,1),(ribbon,1)]),
        ('LAB6_POTTED_PLANT','Растение в подарочном оформлении','medium',
         [(extra['LAB6_PLANT_RESOURCE'],1),(extra['LAB6_POT_RESOURCE'],1),(ribbon,1)]),
        ('LAB6_COMPLEX_ARRANGEMENT','Сложная цветочная композиция','complex',
         [(rose,5),(extra['LAB6_DRY'],2),(extra['LAB6_GREEN'],2),
          (extra['LAB6_SPONGE'],1),(extra['LAB6_BASKET_RESOURCE'],1),(ribbon,1)]),
    ]
    for code,name,qualification,parts in examples:
        pid=product(code,name,assembly,piece,950)
        vid=first_version(pid,[{'component_product_id':p,'quantity':q} for p,q in parts])
        rid=route(vid,'Изготовление: '+name)
        oid=operation(rid,10,
            name='Изготовить изделие',result_name='Готовое изделие: '+name,
            operation_type_id=op_type['assembly'],work_center_id=center['assembly'],
            profession_id=florist,qualification_id=quals[qualification],
            setup_minutes=4,unit_minutes=12)
        for material_id,count in parts:inp(oid,'purchase',material_id,count)
        tool(oid,tools['scissors']);finish(rid,oid)
        result=get(f'/api/routing/routes/{rid}/cost',{'quantity':1})
        print('Additional:',name,'route',rid,'total cost',result['total_cost'],
              'qualification',qualification)

    print('\nPASS: bouquet (1275), nested gift (1330), reusable template size1 (65) size2 (115).')
    print('Open Swagger:',BASE+'/docs')

if __name__=='__main__':
    try:main()
    except (RuntimeError,URLError,AssertionError) as exc:raise SystemExit(str(exc))
