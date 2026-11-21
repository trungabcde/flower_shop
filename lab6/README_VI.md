# Flower Shop — Лабораторная работа №6: Маршрутная технология

Bộ file triển khai thử nghiệm theo **`ТЗ.docx` (30 trang)**, kế thừa Lab 5 mà không thay đổi mã nguồn và bảng của Lab 5. Đây là phần backend + database; không làm giao diện HTML mới.

## 1. Các file

- `lab6_migration.sql` — 19 bảng `routing_*`, constraints và FK; không tạo bảng `products`/BOM thứ hai.
- `lab6_functions.sql` — 5 PostgreSQL functions: `FIND_TM`, `MR_Q`, `SUM_MR`, `TR_TO`, `ROUTING_COST`.
- `routing_lab6.py` — endpoints `/api/routing`, CRUD, lịch trình sản xuất, vật liệu, chi phí, mẫu sản xuất (Flyweight).
- `main_new_lab6.py` — kết hợp app FastAPI Lab 5 và module Lab 6.
- `demo_lab6.py` — chuẩn bị demo qua API, dùng ID do server trả về.
- `test_lab6.py` — kiểm thử cấu trúc, schema API và các công thức chuẩn (không phụ thuộc PostgreSQL).
- `requirements_lab6.txt` — dependencies.
- `Swagger_steps_RU.md` — kịch bản thao tác, chú thích API tiếng Nga.
- `ERD_Lab6.mmd` — sơ đồ quan hệ 19 bảng dưới dạng Mermaid.
- `Реализация_и_отклонения.md` — đối chiếu yêu cầu và các lựa chọn thiết kế, bằng tiếng Nga.

## 2. Sắp xếp trong máy Windows

Đặt thư mục `lab6` cạnh `lab5`, **không đặt `lab6` vào bên trong `lab5`**:

```text
flower_shop/
├── .git/
├── main_new.py
├── catalog1.html
├── lab5/
│   ├── main_new_lab5.py
│   ├── bom_lab5.py
│   ├── demo_lab5.py
│   └── ...
└── lab6/
    ├── main_new_lab6.py
    ├── routing_lab6.py
    ├── lab6_migration.sql
    ├── lab6_functions.sql
    ├── demo_lab6.py
    └── ...
```

## 3. PostgreSQL: nhất thiết sao lưu rồi clone Lab 5

1. Trong DBeaver hoặc pgAdmin, backup database **`flower_shop_lab5`** (đầy đủ schema và data).
2. Tạo database **`flower_shop_lab6`** mới, restore bản backup vừa tạo.
3. Kết nối DBeaver vào `flower_shop_lab6` (không phải `flower_shop_lab5`).
4. Mở `lab6_migration.sql`, chạy **Execute SQL Script** (DBeaver: `Alt+X`).
5. Sau khi thành công, chạy `lab6_functions.sql` bằng cách tương tự.
6. Chạy SQL kiểm tra:

```sql
SELECT table_name FROM information_schema.tables
WHERE table_schema='public' AND table_name LIKE 'routing_%'
ORDER BY table_name;

SELECT routine_name FROM information_schema.routines
WHERE specific_schema='public'
AND LOWER(routine_name) IN ('find_tm','mr_q','sum_mr','tr_to','routing_cost');
```

**Kết quả:** đúng 19 bảng `routing_*` và 5 hàm SQL. Không chạy migration lần đầu trên database gốc kỳ trước.

## 4. Cài Python và khởi động FastAPI

Mở PowerShell **trong `flower_shop\lab6`**:

```powershell
py -m pip install -r requirements_lab6.txt
$env:FLOWER_SHOP_DATABASE_URL = "postgresql+psycopg2://postgres:YOUR_PASSWORD@localhost:5432/flower_shop_lab6"
py -m uvicorn main_new_lab6:app --host 127.0.0.1 --port 8001 --reload
```

Thay `YOUR_PASSWORD` bằng mật khẩu PostgreSQL của bạn. Nếu mật khẩu chứa `@`, `#`, `/`, `:` phải URL-encode trước khi đặt trong connection string. **Không commit mật khẩu lên GitHub**.

Nếu cổng 8001 đang chạy Lab 5, nhấn `Ctrl+C` tại cửa sổ Uvicorn cũ. Chỉ giữ một server trên cổng đó.

Mở **http://127.0.0.1:8001/docs**. Phải nhìn thấy cả `/bom/...` của Lab 5 và `/api/routing/...` của Lab 6.

> Chú ý: tùy chọn là **hai dấu gạch ngang ASCII** `--reload`, không phải dấu dài `–reload`.

## 5. Tạo dữ liệu và tự động kiểm tra tính toán

Mở PowerShell **mới**, cũng tại `flower_shop\lab6` (giữ nguyên server chạy ở cửa sổ thứ nhất):

```powershell
py demo_lab6.py
```

Script tạo dữ liệu qua HTTP API (không chỉnh tay bảng `routing_*`), và dùng dữ liệu Lab 5 cho các danh mục sản phẩm. Các mục demo mang mã `LAB6_`; chạy lại không tạo trùng theo mã/số công đoạn. Kết quả cần thấy:

| Ví dụ | Số lượng | Vật liệu | Lao động | Tổng |
|---|---:|---:|---:|---:|
| `Учебный букет` | 2 | 1080 ₽ | 195 ₽ (39 phút) | **1275 ₽** |
| `Набор с двумя учебными букетами` | 1 | 1110 ₽ | 220 ₽ (44 phút) | **1330 ₽** |
| `Малое оформление` (`size=1`) | 1 | 40 ₽ | 25 ₽ (5 phút) | **65 ₽** |
| `Большое оформление` (`size=2`) | 1 | 80 ₽ | 35 ₽ (7 phút) | **115 ₽** |

**ID trong TЗ (101, 201, 301, 401...) chỉ là ví dụ**. Mã demo lấy mọi `product_id`, `version_id`, `route_id`, `operation_id` từ API thực tế. Nếu bạn đã chạy thử/đổi định mức trước đó, script có thể báo số khác — xem output để điều chỉnh dữ liệu demo trên bản sao database.

## 6. Test không cần PostgreSQL

```powershell
py -m pytest -q test_lab6.py
```

Các test offline chỉ kiểm tra schema, API registration và công thức chuẩn; **không thay thế** việc chạy demo PostgreSQL thật.

## 7. Một số API trong Swagger

- `GET /api/routing/products?search=...` — tìm hàng hóa.
- `GET /api/routing/products/{id}/versions` — lấy `spec_version_id`.
- `GET/POST /api/routing/routes` — xem/tạo маршрут.
- `POST /api/routing/routes/{id}/operations` — thêm công đoạn với tên hành động và tên kết quả.
- `POST /api/routing/operations/{id}/inputs` — gắn vật liệu hoặc kết quả công đoạn trước.
- `POST /api/routing/operations/{id}/tools` — công cụ cần thiết.
- `PATCH /api/routing/routes/{id}` — đặt `final_operation_id`.
- `GET /api/routing/routes/{id}/tree` — xem đầy đủ cây sản xuất.
- `GET /api/routing/routes/{id}/materials?quantity=2` — nguyên liệu và kiểm tra BOM.
- `GET /api/routing/routes/{id}/labor?quantity=2` — tổng thời gian.
- `GET /api/routing/routes/{id}/cost?quantity=2` — tiền vật liệu và nhân công.
- `POST /api/routing/typical-processes` và `/routes/{id}/typical-applications` — шаблон và Flyweight.

`source.kind='purchase'` yêu cầu `product_id`; `source.kind='operation_result'` yêu cầu `operation_id`. Với PATCH/DELETE input, `sourceId` là **`source.id` trong JSON trả về**, không phải `product_id`.

## 8. Quy tắc bảo vệ dữ liệu

- Hệ thống cấm vòng lặp và liên kết tự thân khi tạo đầu vào.
- Nếu маршрут đã được dùng ở маршрут khác: `ROUTE_IN_USE` (HTTP 409).
- Nếu шаблон đã được áp dụng: `TEMPLATE_IN_USE` (HTTP 409).
- Không sửa tay thông số `routing_nodes` được tạo từ шаблон; thay đổi `routing_tp_values` thông qua PATCH application.
- Dữ liệu kỹ thuật được sửa trong một transaction; thất bại sẽ rollback.
- Không tự điền giá vốn thiếu bằng 0 hoặc thay hệ số thiếu bằng 1.
- SQL giá vốn sử dụng riêng `routing_material_prices`, không lấy `products.price` (giá bán).
- Kiểm tra số lượng vật liệu bằng BOM `lab5 /bom/expand`; sản phẩm có variant dùng snapshot phiên bản đã lưu.

## 9. Phân chia để viết báo cáo tiếng Nga

- **Разработка модели хранения:** ER Diagram 19 bảng và FK tới Lab 5.
- **Разработка базы данных:** migration, 5 hàm SQL, dữ liệu mẫu, danh mục.
- **Разработка серверной части:** FastAPI + Swagger, test material/labor/cost, hai tuyến phụ thuộc, typical process size=1/2.
- **Проверка ошибок:** `quantity=-2` → 422; tạo `for_quantity=0` → 422; chu trình; khóa các tuyến và mẫu đã sử dụng.

## 10. Giới hạn và việc cần kiểm tra trước khi nộp

Bộ mã này đã được kiểm tra cú pháp Python và khả năng khởi tạo Swagger/OpenAPI cùng Lab 5 trong môi trường riêng; **chưa được chạy trực tiếp với PostgreSQL của bạn**. Đây là bản triển khai để bạn chạy và kiểm chứng, không nên coi các ảnh/kết quả trong ТЗ là ảnh đã thực nghiệm.

Không bổ sung chức năng login, nhân sự thực tế, trạng thái sản xuất, lịch làm việc, vận chuyển, giao hàng, thanh toán hay giao diện web mới — đúng phạm vi trong ТЗ.
