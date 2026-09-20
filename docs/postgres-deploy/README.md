# PostgreSQL 部署说明（Docker Hub 官方镜像）

选用版本：**`postgres:17.8-alpine`**

对应文件：

- `docker-compose.yml` —— 单机部署文件
- `conf/10-tuning.conf` —— 配置调优片段
- `init/00-include-conf.sh` —— 注入 `include_dir`，让上面的片段生效
- `init/01-init.sql` —— 首次初始化脚本（扩展 / 只读账号 / 超时兜底）

## 1. 为什么选 17.8

| 版本 | 状态 | 说明 |
|---|---|---|
| `postgres:17.8` / `17.8-alpine` | **采用** | 17 系列当前最新小版本，支持到 2029-11 |
| `postgres:18.4` | 最新主版本 | 可用，但数据目录布局有破坏性变更（见下） |
| `postgres:19-beta*` | Beta | 仅测试，勿上生产 |

18 版起官方镜像把 `PGDATA` 从 `/var/lib/postgresql/data` 改成 `/var/lib/postgresql/18/docker`，`VOLUME` 声明同步改成 `/var/lib/postgresql`。沿用旧挂载路径会导致容器启动失败，或数据写进匿名卷、`down` 之后看起来"数据丢了"。

选 17.8 就是绕开这个坑：挂载路径保持经典的 `/var/lib/postgresql/data`，与绝大多数现成教程、脚本、运维习惯一致。

固定小版本（`17.8`）而非浮动主版本（`17`）：前者可复现、便于回滚，后者自动拿到安全补丁。生产建议固定小版本 + 定期人工升版。

## 2. 启动步骤

```bash
sudo mkdir -p /usr/local/service/postgres/{data,init,conf}

docker run --rm postgres:17.8-alpine id postgres
sudo chown -R 70:70 /usr/local/service/postgres

sudo cp conf/10-tuning.conf     /usr/local/service/postgres/conf/
sudo cp init/00-include-conf.sh /usr/local/service/postgres/init/
sudo cp init/01-init.sql        /usr/local/service/postgres/init/
sudo chmod +x /usr/local/service/postgres/init/00-include-conf.sh

docker compose up -d
docker compose logs -f postgres
```

`chown` 的 uid 以 `id postgres` 实测为准：alpine 变体通常是 `70:70`，debian 变体通常是 `999:999`。属主不对会在 `initdb` 阶段报 `could not change permissions of directory`。

`init/00-include-conf.sh` 在 initdb 之后、首次真正启动之前，把 `include_dir = '/etc/postgresql/conf.d'` 追加到 `$PGDATA/postgresql.conf`，让 `conf/10-tuning.conf` 无需手工进容器配置即可生效。它同样只在数据目录为空时执行一次；已初始化过的库要手工补：

```bash
docker compose exec postgres bash -c \
  "grep -q include_dir \$PGDATA/postgresql.conf || \
   echo \"include_dir = '/etc/postgresql/conf.d'\" >> \$PGDATA/postgresql.conf"
docker compose restart postgres
```

## 3. 挂载点

| 容器路径 | 挂载内容 |
|---|---|
| `/var/lib/postgresql/data` | 数据目录（PGDATA） |
| `/docker-entrypoint-initdb.d` | 初始化 SQL/脚本，仅数据目录为空时执行一次 |
| `/etc/postgresql/conf.d` | 配置片段目录 |

## 4. 连接与验证

```bash
psql "postgresql://rag4c:ChangeMe_StrongPwd@localhost:5433/rag4c" -c "select version();"

docker compose ps
docker inspect --format '{{.State.Health.Status}}' postgres
```

RAG4C 侧连接串（驱动为 psycopg）：

```
postgresql+psycopg://rag4c:ChangeMe_StrongPwd@localhost:5433/rag4c
```

容器间互访用服务名和容器内端口，不要用宿主偏移端口：

```
postgresql+psycopg://rag4c:ChangeMe_StrongPwd@postgres:5432/rag4c
```

只读场景用 `rag4c_ro`，连接串可带 `?options=-cdefault_transaction_read_only=on`。

## 5. 备份 / 恢复

```bash
docker compose exec -T postgres pg_dump -U rag4c -d rag4c -Fc > rag4c_$(date +%F).dump
docker compose exec -T postgres pg_dumpall -U rag4c > all_$(date +%F).sql
docker compose exec -T postgres pg_restore -U rag4c -d rag4c --clean --if-exists < rag4c_2026-09-11.dump
```

物理备份：停库后打包宿主机 `data/` 目录。

## 6. 常见坑

1. **数据目录非空导致初始化脚本不执行**：`init/*.sql` 只在 `$PGDATA` 为空时跑，改脚本后需清库重建才生效。
2. **宿主机目录属主不对**：见第 2 节，alpine 常为 `70:70`，debian 为 `999:999`。
3. **`shm_size` 太小**：默认 64MB，并行查询/大排序会报 `could not resize shared memory segment`，已设 512MB。
4. **端口冲突**：宿主 5432 常被本机 PG 占用，本方案统一用 5433。
5. **alpine 变体注意事项**：体积小但基于 musl；需要某些 C 扩展时改用 `postgres:17.8`（Debian）。
6. **容器内时区**：`TZ` 与配置文件里的 `timezone` 都要设，`TZ` 只影响系统层。
7. **大版本升级**：17 → 18 及以上必须用 `pg_dumpall` 导出后导入，不能直接换 tag 复用数据目录。
