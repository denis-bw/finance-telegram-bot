import json
import os
from datetime import date, datetime
import certifi
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId

MONGO_URI = os.getenv("MONGO_URI", "")

DEFAULT_CAT_NAME = "Без категорії"
DEFAULT_CAT_ID: str | None = None

_client: AsyncIOMotorClient | None = None
_db = None


def _get_db():
    global _client, _db
    if _db is None:
        if not MONGO_URI:
            raise ValueError("MONGO_URI не встановлено у змінних середовища.")

        _client = AsyncIOMotorClient(
            MONGO_URI,
            tlsCAFile=certifi.where()
        )
        _db = _client["financebot"]
    return _db


async def init_db():
    global DEFAULT_CAT_ID
    db = _get_db()

    await db.categories.create_index("name", unique=True)
    await db.purchases.create_index([("date", -1)])
    await db.item_categories.create_index(
        [("purchase_id", 1), ("item_index", 1)], unique=True
    )

    existing = await db.categories.find_one({"name": DEFAULT_CAT_NAME})
    if not existing:
        result = await db.categories.insert_one(
            {"name": DEFAULT_CAT_NAME, "is_default": True}
        )
        DEFAULT_CAT_ID = str(result.inserted_id)
    else:
        DEFAULT_CAT_ID = str(existing["_id"])

    await db.purchases.update_many(
        {"category_id": None},
        {"$set": {"category_id": DEFAULT_CAT_ID}}
    )


async def get_default_cat_id() -> str | None:
    global DEFAULT_CAT_ID
    if DEFAULT_CAT_ID is None:
        db = _get_db()
        row = await db.categories.find_one({"name": DEFAULT_CAT_NAME})
        DEFAULT_CAT_ID = str(row["_id"]) if row else None
    return DEFAULT_CAT_ID


def _to_dict(doc) -> dict:
    if doc is None:
        return None
    d = dict(doc)
    d["id"] = str(d.pop("_id"))
    return d


async def get_categories() -> list:
    db = _get_db()
    cursor = db.categories.find().sort([("is_default", -1), ("name", 1)])
    return [_to_dict(r) async for r in cursor]


async def get_category_by_id(cat_id: str) -> dict | None:
    db = _get_db()
    try:
        row = await db.categories.find_one({"_id": ObjectId(cat_id)})
    except Exception:
        return None
    return _to_dict(row)


async def category_exists(name: str) -> bool:
    db = _get_db()
    row = await db.categories.find_one({"name": {"$regex": f"^{name}$", "$options": "i"}})
    return row is not None


async def add_category(name: str) -> str:
    db = _get_db()
    result = await db.categories.insert_one({"name": name, "is_default": False})
    return str(result.inserted_id)


async def rename_category(cat_id: str, new_name: str):
    default_id = await get_default_cat_id()
    if cat_id == default_id:
        raise ValueError("Не можна перейменувати дефолтну категорію.")
    db = _get_db()
    await db.categories.update_one(
        {"_id": ObjectId(cat_id)}, {"$set": {"name": new_name}}
    )


async def delete_category(cat_id: str):
    default_id = await get_default_cat_id()
    if cat_id == default_id:
        raise ValueError("Не можна видалити «Без категорії».")
    db = _get_db()
    await db.purchases.update_many(
        {"category_id": cat_id}, {"$set": {"category_id": default_id}}
    )
    await db.item_categories.update_many(
        {"category_id": cat_id}, {"$set": {"category_id": default_id}}
    )
    await db.categories.delete_one({"_id": ObjectId(cat_id)})


def _normalize_date(raw: str | None) -> str:
    if not raw:
        return str(date.today())
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return str(date.today())


async def add_purchase(data: dict, override_date: str = None, override_time: str = None) -> str:
    default_id = await get_default_cat_id()
    db = _get_db()
    receipt_date = override_date or _normalize_date(data.get("date"))
    receipt_time = override_time or data.get("time") or datetime.now().strftime("%H:%M")
    doc = {
        "store":       data.get("store", "Невідомо"),
        "date":        receipt_date,
        "time":        receipt_time,
        "items":       data.get("items", []),
        "total":       float(data.get("total", 0)),
        "raw_text":    data.get("raw_text", ""),
        "category_id": default_id,
        "created_at":  datetime.utcnow().isoformat(),
        "split_group": None,
    }
    result = await db.purchases.insert_one(doc)
    return str(result.inserted_id)


async def edit_purchase(purchase_id: str, store: str = None, date_str: str = None,
                        time_str: str = None, total: float = None):
    db = _get_db()
    updates = {}
    if store    is not None: updates["store"] = store
    if date_str is not None: updates["date"]  = _normalize_date(date_str)
    if time_str is not None: updates["time"]  = time_str
    if total    is not None: updates["total"] = total
    if updates:
        await db.purchases.update_one(
            {"_id": ObjectId(purchase_id)}, {"$set": updates}
        )


async def set_purchase_category(purchase_id: str, cat_id: str | None):
    effective = cat_id if cat_id else await get_default_cat_id()
    db = _get_db()
    await db.purchases.update_one(
        {"_id": ObjectId(purchase_id)}, {"$set": {"category_id": effective}}
    )


async def set_item_category(purchase_id: str, item_index: int, cat_id: str | None):
    effective = cat_id if cat_id else await get_default_cat_id()
    db = _get_db()
    await db.item_categories.update_one(
        {"purchase_id": purchase_id, "item_index": item_index},
        {"$set": {"category_id": effective}},
        upsert=True
    )


async def get_item_categories(purchase_id: str) -> dict:
    db = _get_db()
    cursor = db.item_categories.find({"purchase_id": purchase_id})
    return {r["item_index"]: r["category_id"] async for r in cursor}


async def get_purchase_by_id(purchase_id: str) -> dict | None:
    db = _get_db()
    try:
        row = await db.purchases.find_one({"_id": ObjectId(purchase_id)})
    except Exception:
        return None
    if not row:
        return None
    d = _to_dict(row)
    cat = await db.categories.find_one({"_id": ObjectId(d["category_id"])}) if d.get("category_id") else None
    d["category_name"] = cat["name"] if cat else DEFAULT_CAT_NAME
    return d


async def get_purchases_by_date(target_date: str) -> list:
    db = _get_db()
    cursor = db.purchases.find({"date": target_date}).sort([("time", 1), ("created_at", 1)])
    result = []
    async for row in cursor:
        d = _to_dict(row)
        cat = await db.categories.find_one({"_id": ObjectId(d["category_id"])}) if d.get("category_id") else None
        d["category_name"] = cat["name"] if cat else DEFAULT_CAT_NAME
        result.append(d)
    return result


async def get_all_purchases_paged(offset: int = 0, limit: int = 8,
                                   cat_filter: str | None = None) -> list:
    db = _get_db()
    query = {"category_id": cat_filter} if cat_filter else {}
    cursor = db.purchases.find(
        query,
        {"store": 1, "date": 1, "time": 1, "total": 1, "category_id": 1}
    ).sort([("date", -1), ("time", -1), ("created_at", -1)]).skip(offset).limit(limit)

    result = []
    async for row in cursor:
        d = _to_dict(row)
        cat = await db.categories.find_one({"_id": ObjectId(d["category_id"])}) if d.get("category_id") else None
        d["category_name"] = cat["name"] if cat else DEFAULT_CAT_NAME
        result.append(d)
    return result


async def count_all_purchases(cat_filter: str | None = None) -> int:
    db = _get_db()
    query = {"category_id": cat_filter} if cat_filter else {}
    return await db.purchases.count_documents(query)


async def delete_purchase(purchase_id: str):
    db = _get_db()
    await db.item_categories.delete_many({"purchase_id": purchase_id})
    await db.purchases.delete_one({"_id": ObjectId(purchase_id)})


async def get_purchases_by_month(year: int, month: int) -> list:
    prefix = f"{year}-{month:02d}"
    db = _get_db()
    cursor = db.purchases.find(
        {"date": {"$regex": f"^{prefix}"}},
    ).sort([("date", 1), ("time", 1)])
    result = []
    async for row in cursor:
        d = _to_dict(row)
        cat = await db.categories.find_one({"_id": ObjectId(d["category_id"])}) if d.get("category_id") else None
        d["category_name"] = cat["name"] if cat else DEFAULT_CAT_NAME
        result.append(d)
    return result


async def get_daily_totals_by_month(year: int, month: int) -> list:
    prefix = f"{year}-{month:02d}"
    db = _get_db()
    pipeline = [
        {"$match": {"date": {"$regex": f"^{prefix}"}}},
        {"$group": {"_id": "$date", "total": {"$sum": "$total"}}},
        {"$sort": {"_id": 1}},
        {"$project": {"date": "$_id", "total": 1, "_id": 0}},
    ]
    return [r async for r in db.purchases.aggregate(pipeline)]


async def get_category_totals_by_month(year: int, month: int) -> list:
    prefix = f"{year}-{month:02d}"
    db = _get_db()
    pipeline = [
        {"$match": {"date": {"$regex": f"^{prefix}"}}},
        {"$group": {"_id": "$category_id", "total": {"$sum": "$total"}}},
        {"$sort": {"total": -1}},
    ]
    rows = [r async for r in db.purchases.aggregate(pipeline)]
    result = []
    for row in rows:
        cid = row["_id"]
        cat = await db.categories.find_one({"_id": ObjectId(cid)}) if cid else None
        result.append({
            "category_name": cat["name"] if cat else DEFAULT_CAT_NAME,
            "total": row["total"],
        })
    return result


async def get_category_totals_by_date(target_date: str) -> list:
    db = _get_db()
    pipeline = [
        {"$match": {"date": target_date}},
        {"$group": {"_id": "$category_id", "total": {"$sum": "$total"}}},
        {"$sort": {"total": -1}},
    ]
    rows = [r async for r in db.purchases.aggregate(pipeline)]
    result = []
    for row in rows:
        cid = row["_id"]
        cat = await db.categories.find_one({"_id": ObjectId(cid)}) if cid else None
        result.append({
            "category_name": cat["name"] if cat else DEFAULT_CAT_NAME,
            "total": row["total"],
        })
    return result


async def get_available_months() -> list:
    db = _get_db()
    pipeline = [
        {"$group": {"_id": {"$substr": ["$date", 0, 7]}}},
        {"$sort": {"_id": -1}},
    ]
    result = []
    async for row in db.purchases.aggregate(pipeline):
        y, m = row["_id"].split("-")
        result.append((int(y), int(m)))
    return result
