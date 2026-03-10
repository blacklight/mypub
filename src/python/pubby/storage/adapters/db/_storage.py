from datetime import datetime, timezone
from typing import Any, Callable

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ...._model import (
    Follower,
    Interaction,
    InteractionStatus,
    InteractionType,
)
from ..._base import ActivityPubStorage
from ._model import (
    DbActivity,
    DbActorCache,
    DbFollower,
    DbInteraction,
)


def _get_upsert_stmt(
    engine: sa.Engine,
    table: sa.Table,
    values: dict[str, Any],
    index_elements: list[str],
    update_columns: list[str],
) -> Any:
    """
    Build a dialect-specific upsert statement.

    For SQLite/PostgreSQL uses INSERT ... ON CONFLICT DO UPDATE.
    Returns None for unsupported dialects (caller should use fallback).
    """
    dialect = engine.dialect.name

    if dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    elif dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        return None  # Unsupported dialect

    stmt = insert(table).values(**values)
    return stmt.on_conflict_do_update(
        index_elements=index_elements,
        set_={col: values[col] for col in update_columns},
    )


class DbActivityPubStorage(ActivityPubStorage):
    """
    SQLAlchemy-based storage backend for ActivityPub data.

    :param engine: SQLAlchemy engine.
    :param follower_model: Mapped model inheriting from DbFollower.
    :param interaction_model: Mapped model inheriting from DbInteraction.
    :param activity_model: Mapped model inheriting from DbActivity.
    :param actor_cache_model: Mapped model inheriting from DbActorCache.
    :param session_factory: SQLAlchemy session factory.
    """

    def __init__(
        self,
        engine: sa.Engine,
        *_,
        follower_model: type[DbFollower],
        interaction_model: type[DbInteraction],
        activity_model: type[DbActivity],
        actor_cache_model: type[DbActorCache],
        session_factory: Callable[[], Session],
        **__,
    ):
        self.engine = engine
        self.session_factory = session_factory
        self.follower_model = follower_model
        self.interaction_model = interaction_model
        self.activity_model = activity_model
        self.actor_cache_model = actor_cache_model

    # ---------- Followers ----------

    def store_follower(self, follower: Follower):
        session = self.session_factory()
        try:
            table = self.follower_model.__table__  # type: ignore
            values = {
                "actor_id": follower.actor_id,
                "inbox": follower.inbox,
                "shared_inbox": follower.shared_inbox,
                "followed_at": follower.followed_at or datetime.now(timezone.utc),
                "actor_data": follower.actor_data or {},
            }

            stmt = _get_upsert_stmt(
                self.engine,
                table,
                values,
                index_elements=["actor_id"],
                update_columns=["inbox", "shared_inbox", "actor_data"],
            )

            if stmt is not None:
                session.execute(stmt)
                session.commit()
            else:
                # Fallback for unsupported dialects
                try:
                    session.add(self.follower_model.from_follower(follower))
                    session.commit()
                except IntegrityError:
                    session.rollback()
                    existing = (
                        session.query(self.follower_model)
                        .filter(self.follower_model.actor_id == follower.actor_id)
                        .one_or_none()
                    )
                    if existing is not None:
                        existing.inbox = follower.inbox
                        existing.shared_inbox = follower.shared_inbox
                        existing.actor_data = follower.actor_data
                        session.commit()
        finally:
            session.close()

    def remove_follower(self, actor_id: str):
        session = self.session_factory()
        try:
            session.query(self.follower_model).filter(
                self.follower_model.actor_id == actor_id
            ).delete(synchronize_session=False)
            session.commit()
        finally:
            session.close()

    def get_followers(self) -> list[Follower]:
        session = self.session_factory()
        try:
            return [
                row.to_follower() for row in session.query(self.follower_model).all()
            ]
        finally:
            session.close()

    # ---------- Interactions ----------

    def store_interaction(self, interaction: Interaction):
        session = self.session_factory()
        now = datetime.now(timezone.utc)
        try:
            table = self.interaction_model.__table__  # type: ignore
            values = {
                "source_actor_id": interaction.source_actor_id,
                "target_resource": interaction.target_resource,
                "interaction_type": interaction.interaction_type,
                "activity_id": interaction.activity_id,
                "object_id": interaction.object_id,
                "content": interaction.content,
                "author_name": interaction.author_name,
                "author_url": interaction.author_url,
                "author_photo": interaction.author_photo,
                "published": interaction.published,
                "status": interaction.status,
                "meta": interaction.metadata or {},
                "created_at": interaction.created_at or now,
                "updated_at": now,
            }

            stmt = _get_upsert_stmt(
                self.engine,
                table,
                values,
                index_elements=[
                    "source_actor_id",
                    "target_resource",
                    "interaction_type",
                ],
                update_columns=[
                    "activity_id",
                    "object_id",
                    "content",
                    "author_name",
                    "author_url",
                    "author_photo",
                    "published",
                    "status",
                    "meta",
                    "updated_at",
                ],
            )

            if stmt is not None:
                session.execute(stmt)
                session.commit()
            else:
                # Fallback for unsupported dialects
                try:
                    session.add(self.interaction_model.from_interaction(interaction))
                    session.commit()
                except IntegrityError:
                    session.rollback()
                    existing = (
                        session.query(self.interaction_model)
                        .filter(
                            sa.and_(
                                self.interaction_model.source_actor_id
                                == interaction.source_actor_id,
                                self.interaction_model.target_resource
                                == interaction.target_resource,
                                self.interaction_model.interaction_type
                                == interaction.interaction_type,
                            )
                        )
                        .one_or_none()
                    )
                    if existing is not None:
                        existing.activity_id = interaction.activity_id
                        existing.object_id = interaction.object_id
                        existing.content = interaction.content
                        existing.author_name = interaction.author_name
                        existing.author_url = interaction.author_url
                        existing.author_photo = interaction.author_photo
                        existing.published = interaction.published
                        existing.status = interaction.status
                        existing.meta = interaction.metadata or {}
                        existing.updated_at = now
                        session.commit()

            # Store mentions if model is configured
            if (
                self.interaction_mention_model is not None
                and interaction.mentioned_actors
            ):
                # Fetch the interaction ID for mention storage
                db_interaction = (
                    session.query(self.interaction_model)
                    .filter(
                        sa.and_(
                            self.interaction_model.source_actor_id
                            == interaction.source_actor_id,
                            self.interaction_model.target_resource
                            == interaction.target_resource,
                            self.interaction_model.interaction_type
                            == interaction.interaction_type,
                        )
                    )
                    .one_or_none()
                )
                if db_interaction is not None:
                    self._store_mentions(
                        session, db_interaction.id, interaction.mentioned_actors
                    )
        finally:
            session.close()

    def delete_interaction(
        self,
        source_actor_id: str,
        target_resource: str,
        interaction_type: InteractionType,
    ):
        session = self.session_factory()
        try:
            existing = (
                session.query(self.interaction_model)
                .filter(
                    sa.and_(
                        self.interaction_model.source_actor_id == source_actor_id,
                        self.interaction_model.target_resource == target_resource,
                        self.interaction_model.interaction_type == interaction_type,
                    )
                )
                .one_or_none()
            )
            if existing is not None:
                existing.status = InteractionStatus.DELETED
                existing.updated_at = datetime.now(timezone.utc)
                session.commit()
        finally:
            session.close()

    def delete_interaction_by_object_id(
        self,
        source_actor_id: str,
        object_id: str,
    ) -> bool:
        session = self.session_factory()
        try:
            rows = (
                session.query(self.interaction_model)
                .filter(
                    sa.and_(
                        self.interaction_model.source_actor_id == source_actor_id,
                        self.interaction_model.object_id == object_id,
                        self.interaction_model.status != InteractionStatus.DELETED,
                    )
                )
                .all()
            )
            if not rows:
                return False
            for row in rows:
                row.status = InteractionStatus.DELETED
                row.updated_at = datetime.now(timezone.utc)
            session.commit()
            return True
        finally:
            session.close()

    def get_interactions(
        self,
        target_resource: str,
        interaction_type: InteractionType | None = None,
        status: InteractionStatus = InteractionStatus.CONFIRMED,
    ) -> list[Interaction]:
        session = self.session_factory()
        try:
            query = session.query(self.interaction_model).filter(
                sa.and_(
                    self.interaction_model.target_resource == target_resource,
                    self.interaction_model.status == status,
                )
            )
            if interaction_type is not None:
                query = query.filter(
                    self.interaction_model.interaction_type == interaction_type
                )
            return [row.to_interaction() for row in query.all()]
        finally:
            session.close()

    # ---------- Activities ----------

    def store_activity(self, activity_id: str, activity_data: dict):
        session = self.session_factory()
        now = datetime.now(timezone.utc)
        try:
            table = self.activity_model.__table__  # type: ignore
            values = {
                "activity_id": activity_id,
                "activity_data": activity_data,
                "created_at": now,
            }

            stmt = _get_upsert_stmt(
                self.engine,
                table,
                values,
                index_elements=["activity_id"],
                update_columns=["activity_data"],
            )

            if stmt is not None:
                session.execute(stmt)
                session.commit()
            else:
                # Fallback for unsupported dialects
                try:
                    session.add(
                        self.activity_model(
                            activity_id=activity_id,
                            activity_data=activity_data,
                            created_at=now,
                        )
                    )
                    session.commit()
                except IntegrityError:
                    session.rollback()
                    existing = (
                        session.query(self.activity_model)
                        .filter(self.activity_model.activity_id == activity_id)
                        .one_or_none()
                    )
                    if existing is not None:
                        existing.activity_data = activity_data
                        session.commit()
        finally:
            session.close()

    def get_activities(
        self,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict]:
        session = self.session_factory()
        try:
            rows = (
                session.query(self.activity_model)
                .order_by(self.activity_model.created_at.desc())
                .offset(offset)
                .limit(limit)
                .all()
            )
            return [dict(row.activity_data) for row in rows]
        finally:
            session.close()

    # ---------- Actor cache ----------

    def cache_remote_actor(
        self,
        actor_id: str,
        actor_data: dict,
        fetched_at: datetime | None = None,
    ):
        now = fetched_at or datetime.now(timezone.utc)
        session = self.session_factory()
        try:
            table = self.actor_cache_model.__table__  # type: ignore
            values = {
                "actor_id": actor_id,
                "actor_data": actor_data,
                "fetched_at": now,
            }

            stmt = _get_upsert_stmt(
                self.engine,
                table,
                values,
                index_elements=["actor_id"],
                update_columns=["actor_data", "fetched_at"],
            )

            if stmt is not None:
                session.execute(stmt)
                session.commit()
            else:
                # Fallback for unsupported dialects
                try:
                    session.add(
                        self.actor_cache_model(
                            actor_id=actor_id,
                            actor_data=actor_data,
                            fetched_at=now,
                        )
                    )
                    session.commit()
                except IntegrityError:
                    session.rollback()
                    existing = (
                        session.query(self.actor_cache_model)
                        .filter(self.actor_cache_model.actor_id == actor_id)
                        .one_or_none()
                    )
                    if existing is not None:
                        existing.actor_data = actor_data
                        existing.fetched_at = now
                        session.commit()
        finally:
            session.close()

    def get_cached_actor(
        self,
        actor_id: str,
        max_age_seconds: float = 86400.0,
    ) -> dict | None:
        session = self.session_factory()
        try:
            row = (
                session.query(self.actor_cache_model)
                .filter(self.actor_cache_model.actor_id == actor_id)
                .one_or_none()
            )
            if row is None:
                return None

            age = (
                datetime.now(timezone.utc) - row.fetched_at.replace(tzinfo=timezone.utc)
            ).total_seconds()
            if age > max_age_seconds:
                return None

            return dict(row.actor_data)
        finally:
            session.close()

    # ---------- Quote authorizations ----------

    def store_quote_authorization(
        self,
        authorization_id: str,
        authorization_data: dict,
    ):
        self.store_activity(authorization_id, authorization_data)

    def get_quote_authorization(self, authorization_id: str) -> dict | None:
        session = self.session_factory()
        try:
            row = (
                session.query(self.activity_model)
                .filter(self.activity_model.activity_id == authorization_id)
                .one_or_none()
            )
            if row is None:
                return None
            return dict(row.activity_data)
        finally:
            session.close()
