from app.db.engine import get_db
from app.db.models.models import Model
from sqlalchemy import text


def get_model_by_provider_and_name(provider_id: int, model_name: str):
    db = next(get_db())
    try:
        model = db.query(Model).filter_by(provider_id=provider_id, model_name=model_name).first()
        if model:
            return {
                "id": model.id,
                "provider_id": model.provider_id,
                "model_name": model.model_name,
                "created_at": model.created_at,
            }
        return None
    finally:
        db.close()


def insert_model(provider_id: int, model_name: str):
    db = next(get_db())
    try:
        model = Model(provider_id=provider_id, model_name=model_name)
        db.add(model)
        db.commit()
        db.refresh(model)
        return {
            "id": model.id,
            "provider_id": model.provider_id,
            "model_name": model.model_name,
            "created_at": model.created_at,
        }
    finally:
        db.close()


def get_models_by_provider(provider_id: int):
    db = next(get_db())
    try:
        models = db.query(Model).filter_by(provider_id=provider_id).all()
        return [{"id": m.id, "model_name": m.model_name} for m in models]
    finally:
        db.close()


def delete_model(model_id: int):
    db = next(get_db())
    try:
        model = db.query(Model).filter_by(id=model_id).first()
        if model:
            db.delete(model)
            db.commit()
    finally:
        db.close()


def get_all_models():
    db = next(get_db())
    try:
        models = db.query(Model).all()
        return [
            {"id": m.id, "provider_id": m.provider_id, "model_name": m.model_name}
            for m in models
        ]
    finally:
        db.close()


def delete_models_by_provider(provider_id: str | int) -> int:
    """
    Delete all models for a provider.

    Note: the schema currently uses an Integer column for provider_id, but SQLite
    may store string ids; raw SQL keeps this compatible.
    """
    db = next(get_db())
    try:
        result = db.execute(
            text("DELETE FROM models WHERE provider_id = :provider_id"),
            {"provider_id": provider_id},
        )
        db.commit()
        # SQLAlchemy result.rowcount may be -1 for some DBs/drivers.
        return int(result.rowcount or 0)
    finally:
        db.close()
