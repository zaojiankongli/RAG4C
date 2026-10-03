from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from models.orm import Base

config = context.config
if config.config_file_name is not None:
    # disable_existing_loggers 必须关：fileConfig 默认会把「此刻已存在的全部
    # logger」置为 disabled——进程内任何一次 upgrade（测试、seed、运行期自动
    # 升级）都会把在此之前创建的 rag4c.* 运行日志整体静默，且无任何报错。
    # alembic 自己的 alembic.* 命名空间仍按 alembic.ini 配置生效。
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
