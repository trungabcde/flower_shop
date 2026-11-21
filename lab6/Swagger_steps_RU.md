# Практическая работа № 6. Сценарий демонстрации через Swagger

**База:** `flower_shop_lab6` (копия Lab 5). **Адрес:** `http://127.0.0.1:8001/docs`. **Все ID берите из ответов текущей БД**, а не из номеров примера ТЗ (101, 201, 301 и т. д.). Сначала рекомендуется выполнить `py demo_lab6.py`, чтобы подготовить примеры и проверить контрольные суммы.

## 1. Подготовка справочников

**1.1. GET `/api/routing/departments`** — HTTP 200: `workshop — Флористическая мастерская`.

**1.2. GET `/api/routing/work-centers`** — HTTP 200: `preparation`, `assembly`, `packaging` (три станции; тип «Рабочее место флориста»). Сохранить ID каждой станции.

**1.3. GET `/api/routing/operation-types`** — HTTP 200: `preparation`, `assembly`, `packaging`, `completion`. Сохранить ID типов.

**1.4. GET `/api/routing/professions`** — HTTP 200: `florist — Флорист`, `base_hourly_rate=300`.

**1.5. GET `/api/routing/qualifications`** — HTTP 200: `simple=1`, `medium=1.5`, `complex=2`.

**1.6. GET `/api/routing/tools`** — HTTP 200: секатор, флористический нож, ножницы.

**1.7. GET `/api/routing/products?search=Учебный`** — HTTP 200: найдите `Учебный букет` (код `LAB6_BOUQUET`). Из ответа возьмите `id`.

**1.8. GET `/api/routing/products/{id}/versions`** — HTTP 200: выберите версию, получите `id` спецификации.

**1.9. GET `/api/routing/material-prices`** — HTTP 200: роза 100 руб./шт., упаковка 20 руб./лист, лента 10 руб./м. Это *учетные* цены, а не `products.price`.

## 2. Создание маршрута для учебного букета

При повторной демонстрации можно показывать уже созданные записи через GET, не плодя дубликаты POST.

**2.1. POST `/api/routing/routes`** — HTTP 201. JSON (подставить реальный `SPEC_VERSION_ID`):

```json
{
  "name": "Изготовление учебного букета",
  "spec_version_id": 201,
  "variant_id": null
}
```

Число `201` **условное**, его нельзя использовать без проверки! Из ответа записать `route.id`.

**2.2. POST `/api/routing/routes/{routeId}/operations`** — три операции по очереди; в каждом POST в path укажите ID маршрута, а внутри — ID станций/типов/профессии/квалификации из GET. Образец для подготовки (все ID условны):

```json
{
  "number": 10,
  "name": "Подготовить цветы",
  "result_name": "Подготовленные цветы для букета",
  "description": "Подрезать стебли и удалить лишние листья",
  "operation_type_id": 1,
  "work_center_id": 1,
  "profession_id": 1,
  "qualification_id": 1,
  "setup_minutes": 2,
  "unit_minutes": 2
}
```

Аналогично, `number=20`, `name=Собрать букет`, `result_name=Собранный букет`, станция сборки, `setup_minutes=5`, `unit_minutes=10`. Далее `number=30`, `name=Упаковать букет`, станция упаковки, `setup_minutes=2`, `unit_minutes=3`. Каждый POST возвращает **HTTP 201** и `id` созданной операции: запишите `PREP_ID`, `ASSEMBLY_ID`, `PACK_ID`.

**2.3. POST `/api/routing/operations/{PREP_ID}/inputs`** — розы на подготовку, HTTP 201:

```json
{"source":{"kind":"purchase","product_id":11},"quantity":5,"for_quantity":1}
```

Число `11` замените на **настоящий ID** продукта `LAB6_ROSE`, найденный через `/api/routing/products?search=Роза`. В ответе `source.id` — внутренний ID закупного узла, это не ID товара.

**2.4. POST `/api/routing/operations/{ASSEMBLY_ID}/inputs`** — результат подготовки на сборку, HTTP 201:

```json
{"source":{"kind":"operation_result","operation_id":401},"quantity":1,"for_quantity":1}
```

Замените `401` на реальный `PREP_ID`.

**2.5. POST `/api/routing/operations/{PACK_ID}/inputs`** — три входа, каждый HTTP 201:

```json
{"source":{"kind":"operation_result","operation_id":402},"quantity":1,"for_quantity":1}
```

Замените `402` на `ASSEMBLY_ID`.

```json
{"source":{"kind":"purchase","product_id":12},"quantity":1,"for_quantity":1}
```

Замените `12` на ID `LAB6_WRAP`.

```json
{"source":{"kind":"purchase","product_id":13},"quantity":2,"for_quantity":1}
```

Замените `13` на ID `LAB6_RIBBON`.

**2.6. POST `/api/routing/operations/{PREP_ID}/tools`** — HTTP 201:

```json
{"tool_id":1}
```

Заменить `1` на реальный ID `Секатор` из GET `/api/routing/tools`. Аналогично к упаковке привязать ножницы.

**2.7. PATCH `/api/routing/routes/{routeId}`** — HTTP 200:

```json
{"final_operation_id":403}
```

Заменить `403` на `PACK_ID`.

## 3. Проверка расчётов

**3.1. GET `/api/routing/routes/{routeId}/tree`** — HTTP 200. Полный граф от упаковки до покупных материалов, читаемые имена, инструменты, нормы.

**3.2. GET `/api/routing/operations/{PACK_ID}/materials?quantity=2`** — HTTP 200; непосредственные входы: 2 собранных букета, 2 листа упаковки, 4 м ленты.

**3.3. GET `/api/routing/routes/{routeId}/materials?quantity=2`** — HTTP 200; суммарно: 10 роз, 2 листа упаковки, 4 м ленты. Проверяется соответствие спецификации BOM.

**3.4. GET `/api/routing/routes/{routeId}/labor?quantity=2`** — HTTP 200; подготовка **6 мин.**, сборка **25 мин.**, упаковка **8 мин.**, всего **39 мин.**

**3.5. GET `/api/routing/routes/{routeId}/cost?quantity=2`** — HTTP 200; `material_cost=1080`, `labor_cost=195`, `total_cost=1275`, `pricing="current"`.

Формулы: `потребность = требуемый_объем × quantity / for_quantity`; `T=setup_minutes + unit_minutes × объем`; ставка = 300 × 1; стоимость материалов = 10×100 + 2×20 + 4×10.

## 4. Вложенный маршрут

Через GET `/api/routing/products?search=Набор с двумя` найдите изделие `LAB6_GIFT`, затем GET versions → GET routes. В его маршруте одна операция комплектации, требующая **2 результата операции упаковки** из маршрута учебного букета и **1 открытку**. Для одного набора:

- Материалы 10 роз, 2 упаковки, 4 м ленты, 1 открытка = **1110 руб.**
- Труд: 39 + 5 = **44 мин.** = **220 руб.**
- Общая стоимость **1330 руб.**

Попробуйте изменить уже вложенный маршрут букета с помощью PATCH операции. Ожидается **HTTP 409, `ROUTE_IN_USE`**.

## 5. Типовой технологический процесс (паттерн «Приспособленец»)

**5.1. GET `/api/routing/typical-processes`** → найдите `Оформление букета`. GET `/api/routing/typical-processes/{id}` → общий шаблон.

**5.2. GET `/api/routing/typical-processes/{id}/parameters`** → параметр `size` с границами 1..2, default=1, integer_only=true.

**5.3. GET `/api/routing/typical-processes/{id}/operations`** → общая операция `Упаковать букет`, формула времени `Tшт=1+2×size` и подготовка `Tпз=2`.

**5.4. GET `/api/routing/typical-operations/{id}/inputs`** → `упаковка=size`, `лента=2×size`.

**5.5. GET `/api/routing/routes?product_id={smallId}`** → маршрут `Малое оформление`, а GET `/routes/{id}/typical-applications` покажет `size=1`.

**5.6. GET `/api/routing/routes?product_id={largeId}`** → маршрут `Большое оформление`, GET application → `size=2`. Оба применения имеют одинаковый `process.id` и одинаковые `template_operation_id`, но разные параметры.

**5.7. GET `/api/routing/routes/{smallRoute}/cost?quantity=1`** → **65 руб.**. Для большого → **115 руб.**

**5.8. PATCH используемого шаблона** → HTTP **409**, `TEMPLATE_IN_USE`.

## 6. Отрицательные тесты

- GET `/api/routing/routes/{routeId}/materials?quantity=-2` → HTTP **422** `INVALID_QUANTITY`.
- POST `/api/routing/operations/{id}/inputs` с `for_quantity=0` → HTTP **422** `INVALID_FOR_QUANTITY`.
- POST входа `operation_result` из операции в саму себя → HTTP **409** `CYCLE_DETECTED`.
- PATCH операции вложенного маршрута → HTTP **409** `ROUTE_IN_USE`.
- PATCH используемого шаблона → HTTP **409** `TEMPLATE_IN_USE`.
- PATCH application `parameters.size=3` → HTTP **422** `PARAMETER_OUT_OF_RANGE`.
- Отсутствующая учетная цена материала → ошибка `MISSING_MATERIAL_PRICE`, не нулевая стоимость.

**Для каждого скриншота**: название URL, запрос/параметры, HTTP-статус, JSON-ответ с названиями ресурсов. По возможности сохраните изображения для отчета из Swagger и ERD из DBeaver.

## 7. Что не делать

Не реализовывать производственные заказы, фактическое исполнение, сотрудников, назначения и смены, авторизацию, курьерскую логистику, стоимость доставки, наценки и отдельную таблицу ставок/коэффициентов. Это прямо исключено из ТЗ.
