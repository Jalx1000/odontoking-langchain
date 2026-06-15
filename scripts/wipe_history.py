#!/usr/bin/env python
"""Borra TODO el historial conversacional del agente (Postgres + Redis).

Diseñado para correr desde tu consola local contra los endpoints PÚBLICOS de Railway.
Las URLs se pasan por variables de entorno (NUNCA por argumento, para que no queden en el
historial del shell):

    WIPE_DATABASE_URL   URL pública de Postgres (servicio pgvector → DATABASE_URL)
    WIPE_REDIS_URL      URL pública de Redis    (servicio Redis    → REDIS_PUBLIC_URL)

Qué borra
---------
Postgres:  checkpoints, checkpoint_blobs, checkpoint_writes (LangGraph checkpoints),
           chat_histories_odonto (historial de chat), longterm_memory (mem0).
Redis:     intake:*, wa_incoming:*, wa_worker:*, wa:*  (intake, buffers, streams, locks, DLQ).

Qué NO toca
-----------
Postgres:  user, tenants, session, usage_logs, thread (y cualquier otra tabla).
Redis:     tenant:config:*  (caché de configuración) y todo lo demás.

Uso (vía Makefile: `make history-check` / `make history-wipe`)
    python scripts/wipe_history.py --dry-run         # solo cuenta, no borra
    python scripts/wipe_history.py --apply           # respalda, pide confirmación y borra
    python scripts/wipe_history.py --apply --yes     # sin pregunta interactiva
    python scripts/wipe_history.py --apply --no-backup
"""

import argparse
import datetime as dt
import os
import sys
from pathlib import Path

import psycopg
import redis

PG_TABLES = [
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
    "chat_histories_odonto",
    "longterm_memory",
]

# Tablas que jamás se tocan (solo informativo / sanity guard).
PG_PRESERVE = ["user", "tenants", "session", "usage_logs", "thread"]

REDIS_PATTERNS = ["intake:*", "wa_incoming:*", "wa_worker:*", "wa:*"]


def _die(msg: str) -> None:
    print(f"✗ {msg}", file=sys.stderr)
    sys.exit(1)


def _table_exists(conn: "psycopg.Connection", table: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s)", (f"public.{table}",))
        return cur.fetchone()[0] is not None


def _count_rows(conn: "psycopg.Connection", table: str) -> int:
    with conn.cursor() as cur:
        cur.execute(f'SELECT count(*) FROM "{table}"')  # noqa: S608 - table from fixed allowlist
        return int(cur.fetchone()[0])


def _count_redis(r: "redis.Redis", pattern: str) -> int:
    return sum(1 for _ in r.scan_iter(match=pattern, count=500))


def survey(conn, r) -> tuple[dict, dict]:
    """Return ({table: rows}, {pattern: keys}) for the conversational data."""
    pg = {}
    for t in PG_TABLES:
        pg[t] = _count_rows(conn, t) if _table_exists(conn, t) else None
    rd = {p: _count_redis(r, p) for p in REDIS_PATTERNS}
    return pg, rd


def print_plan(pg: dict, rd: dict) -> None:
    """Print what would be deleted (per Postgres table and Redis pattern)."""
    print("\nPostgres (servicio pgvector):")
    for t, n in pg.items():
        label = "(no existe)" if n is None else f"{n} filas"
        print(f"  - {t:<24} {label}")
    print("  preservadas (NO se tocan): " + ", ".join(PG_PRESERVE))
    print("\nRedis (servicio Redis):")
    for p, n in rd.items():
        print(f"  - {p:<16} {n} claves")
    print("  preservadas (NO se tocan): tenant:config:*  (caché de configuración)")


def backup_pg(conn, backup_dir: Path, pg: dict) -> None:
    """Dump each target table to CSV (restorable via COPY FROM)."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    for t, n in pg.items():
        out = backup_dir / f"{t}.csv"
        if n is None:
            out.write_text("")  # tabla inexistente → archivo vacío como marcador
            continue
        with conn.cursor() as cur, open(out, "wb") as f:
            with cur.copy(f'COPY (SELECT * FROM "{t}") TO STDOUT WITH (FORMAT csv, HEADER)') as cp:  # noqa: S608
                for chunk in cp:
                    f.write(bytes(chunk))
        print(f"  respaldado {t} → {out} ({n} filas)")


def wipe_pg(conn, pg: dict) -> dict:
    """DELETE all rows from each existing target table; return rows deleted per table."""
    deleted = {}
    with conn.cursor() as cur:
        for t, n in pg.items():
            if n is None:
                deleted[t] = None
                continue
            cur.execute(f'DELETE FROM "{t}"')  # noqa: S608 - table from fixed allowlist
            deleted[t] = cur.rowcount
    conn.commit()
    return deleted


def wipe_redis(r, rd: dict) -> dict:
    """Delete every key matching each conversational pattern; return keys deleted per pattern."""
    deleted = {}
    for p in rd:
        keys = list(r.scan_iter(match=p, count=500))
        if keys:
            # borrar en lotes para no mandar un DEL gigante
            for i in range(0, len(keys), 500):
                r.delete(*keys[i : i + 500])
        deleted[p] = len(keys)
    return deleted


def main() -> None:
    """CLI entrypoint: survey, optionally back up, confirm, delete, and verify."""
    ap = argparse.ArgumentParser(description="Borra el historial conversacional del agente (Postgres + Redis).")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", help="Solo cuenta, no borra (por defecto).")
    g.add_argument("--apply", action="store_true", help="Respalda y borra.")
    ap.add_argument("--yes", action="store_true", help="No preguntar; asumir confirmación.")
    ap.add_argument("--no-backup", action="store_true", help="No respaldar antes de borrar.")
    ap.add_argument(
        "--backup-dir",
        default=None,
        help="Carpeta de respaldo (default: odontoking-backups/history-<timestamp>).",
    )
    args = ap.parse_args()

    db_url = os.getenv("WIPE_DATABASE_URL")
    redis_url = os.getenv("WIPE_REDIS_URL")
    if not db_url:
        _die("Falta WIPE_DATABASE_URL (URL pública de Postgres / servicio pgvector → DATABASE_URL).")
    if not redis_url:
        _die("Falta WIPE_REDIS_URL (URL pública de Redis / servicio Redis → REDIS_PUBLIC_URL).")

    print("Conectando a producción (endpoints públicos de Railway)…")
    try:
        conn = psycopg.connect(db_url, connect_timeout=15)
    except Exception as e:  # noqa: BLE001
        _die(f"No pude conectar a Postgres: {e}")
    try:
        r = redis.from_url(redis_url, socket_connect_timeout=15, decode_responses=True)
        r.ping()
    except Exception as e:  # noqa: BLE001
        _die(f"No pude conectar a Redis: {e}")

    pg, rd = survey(conn, r)
    print_plan(pg, rd)

    total = sum(n for n in pg.values() if n) + sum(rd.values())
    if not args.apply:
        print(f"\n[DRY-RUN] Total a borrar: {total} (Postgres filas + Redis claves). No se borró nada.")
        print("Para borrar: make history-wipe   (o: python scripts/wipe_history.py --apply)")
        return

    if total == 0:
        print("\nNada que borrar: ya está todo en 0. ✅")
        return

    # Respaldo
    if not args.no_backup:
        bdir = Path(
            args.backup_dir
            or f"odontoking-backups/history-{dt.datetime.now():%Y%m%d-%H%M%S}"
        ).expanduser()
        print(f"\nRespaldando Postgres en {bdir} …")
        backup_pg(conn, bdir, pg)
        print(f"  ⚠️  El respaldo contiene PII de pacientes. Guárdalo fuera del repo: {bdir.resolve()}")
    else:
        print("\n(omitido respaldo por --no-backup)")

    # Confirmación
    if not args.yes:
        ans = input(f"\n¿Borrar definitivamente {total} elementos en PRODUCCIÓN? (escribe 'si'): ").strip().lower()
        if ans not in ("si", "sí", "yes", "y"):
            print("Cancelado. No se borró nada.")
            return

    print("\nBorrando…")
    dpg = wipe_pg(conn, pg)
    drd = wipe_redis(r, rd)
    for t, n in dpg.items():
        if n is not None:
            print(f"  Postgres {t}: {n} filas borradas")
    for p, n in drd.items():
        print(f"  Redis {p}: {n} claves borradas")

    # Verificación
    pg2, rd2 = survey(conn, r)
    left = sum(n for n in pg2.values() if n) + sum(rd2.values())
    conn.close()
    if left == 0:
        print("\n✅ Verificado: todo el historial conversacional está en 0.")
    else:
        print(f"\n⚠️  Quedaron {left} elementos. Revisa la salida de arriba.")
        sys.exit(2)


if __name__ == "__main__":
    main()
