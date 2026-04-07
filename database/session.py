from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.pool import StaticPool
from config import settings


Base = declarative_base()


def _make_engine(url: str):
    if url.startswith("sqlite"):
        engine = create_engine(
            url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool if url == "sqlite://" else None,
            future=True,
        )
    else:
        engine = create_engine(url, pool_pre_ping=True, future=True)
    return engine


engine = _make_engine(settings.database_url)
SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    future=True,
    expire_on_commit=False,
)


def init_db():
    from . import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
