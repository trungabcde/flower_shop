"""Entry point: shared Lab 5 Flower Shop + Lab 6 routing API.

Run from flower_shop/lab6:
    py -m uvicorn main_new_lab6:app --host 127.0.0.1 --port 8001 --reload
Environment: FLOWER_SHOP_DATABASE_URL to an already migrated Lab 6 copy of Lab 5 DB.
"""
import sys
from pathlib import Path

LAB5_DIR = Path(__file__).resolve().parent.parent / 'lab5'
if not (LAB5_DIR / 'main_new_lab5.py').exists():
    raise RuntimeError(f'Lab 5 files not found in {LAB5_DIR}; copy your previous lab5 folder there.')
if str(LAB5_DIR) not in sys.path:
    sys.path.insert(0, str(LAB5_DIR))

from main_new_lab5 import app, get_db  # noqa: E402
from routing_lab6 import install_routing  # noqa: E402

install_routing(app, get_db)
app.title = 'Flower Shop — BOM (Lab 5) + Routing (Lab 6)'
app.version = '3.0'

if __name__ == '__main__':
    import uvicorn
    uvicorn.run('main_new_lab6:app',host='127.0.0.1',port=8001,reload=False)
