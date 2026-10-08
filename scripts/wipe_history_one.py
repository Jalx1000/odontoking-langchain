#!/usr/bin/env python
"""Borra el historial conversacional de UN solo número (wa_id), no de todos.

Mismo almacenamiento que wipe_history.py, pero filtrado por thread_id/session_id = wa_id. Pensado para
correr desde tu consola local contra los endpoints PÚBLICOS de Railway. Las URLs van por variable de
entorno (nunca por argumento, para que no queden en el historial del shell):

    WIPE_DATABASE_URL   URL pública de Postgres del tenant (p. ej. Imprimir Bd → DATABASE_PUBLIC_URL)
    WIPE_REDIS_URL      URL pública de Redis (servicio Redis → REDIS_PUBLIC_URL)

Qué borra (solo del wa_id dado)
-------------------------------
Postgres:  checkpoints, checkpoint_blobs, checkpoint_writes (por thread_id),
           chat_histories_odonto (por session_id), longterm_memory (si el payload menciona el wa_id).
Redis:     claves intake:* / wa_incoming:* / wa_worker:* / wa:* que contengan el wa_id.

Uso
    python scripts/wipe_history_one.py <wa_id>            # dry-run (solo cuenta)
    python scripts/wipe_history_one.py <wa_id> --apply    # borra (pide confirmación)
    python scripts/wipe_history_one.py <wa_id> --apply --yes
"""

import argparse
import os
import sys

import psycopg
import redis

# (tabla, columna-clave). longterm_memory no tiene columna directa → se filtra por payload.
PG_BY_KEY = [
    ("checkpoints", "thread_id"),
    ("checkpoint_blobs", "thread_id"),
    ("checkpoint_writes", "thread_id"),
    ("chat_histories_odonto", "session_id"),
]
PG_MEMORY = "longterm_memory"
REDIS_PREFIXES = ["intake:*", "wa_incoming:*", "wa_worker:*", "wa:*"]


def _die(msg: str) -> None:
    print(f"✗ {msg}", file=sys.stderr)
    sys.exit(1)


def _exists(conn, table: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s)", (f"public.{table}",))
        return cur.fetchone()[0] is not None


def survey_pg(conn, wa: str) -> dict:
    """Count rows for this wa_id per table (None = table missing)."""
    out: dict[str, int | None] = {}
    with conn.cursor() as cur:
        for table, key in PG_BY_KEY:
            if not _exists(conn, table):
                out[table] = None
                continue
            cur.execute(f'SELECT count(*) FROM "{table}" WHERE {key} = %s', (wa,))  # noqa: S608
            out[table] = int(cur.fetchone()[0])
        if _exists(conn, PG_MEMORY):
            cur.execute(f'SELECT count(*) FROM "{PG_MEMORY}" WHERE payload::text LIKE %s', (f"%{wa}%",))  # noqa: S608
            out[PG_MEMORY] = int(cur.fetchone()[0])
        else:
            out[PG_MEMORY] = None
    return out


def survey_redis(r, wa: str) -> dict:
    """List matching keys per prefix that contain the wa_id."""
    return {p: [k for k in r.scan_iter(match=p, count=500) if wa in k] for p in REDIS_PREFIXES}


def wipe_pg(conn, wa: str) -> dict:
    """DELETE this wa_id's rows; return rows deleted per table."""
    deleted: dict[str, int | None] = {}
    with conn.cursor() as cur:
        for table, key in PG_BY_KEY:
            if not _exists(conn, table):
                deleted[table] = None
                continue
            cur.execute(f'DELETE FROM "{table}" WHERE {key} = %s', (wa,))  # noqa: S608
            deleted[table] = cur.rowcount
        if _exists(conn, PG_MEMORY):
            cur.execute(f'DELETE FROM "{PG_MEMORY}" WHERE payload::text LIKE %s', (f"%{wa}%",))  # noqa: S608
            deleted[PG_MEMORY] = cur.rowcount
    conn.commit()
    return deleted


def wipe_redis(r, keys_by_prefix: dict) -> dict:
    """Delete the matched keys; return count deleted per prefix."""
    deleted = {}
    for p, keys in keys_by_prefix.items():
        if keys:
            r.delete(*keys)
        deleted[p] = len(keys)
    return deleted


def main() -> None:
    """CLI: survey one wa_id, confirm, delete, verify."""
    ap = argparse.ArgumentParser(description="Borra el historial de UN número (wa_id).")
    ap.add_argument("wa_id", help="El wa_id completo, p. ej. 59176616013.")
    ap.add_argument("--apply", action="store_true", help="Borra (si no, solo cuenta).")
    ap.add_argument("--yes", action="store_true", help="No preguntar; asumir confirmación.")
    args = ap.parse_args()
    wa = args.wa_id.strip()
    if not wa.isdigit():
        _die(f"wa_id inválido: {wa!r} (debe ser solo dígitos, p. ej. 59176616013).")

    db_url = os.getenv("WIPE_DATABASE_URL") or _die("Falta WIPE_DATABASE_URL.")
    redis_url = os.getenv("WIPE_REDIS_URL") or _die("Falta WIPE_REDIS_URL.")

    conn = psycopg.connect(db_url, connect_timeout=20)
    r = redis.from_url(redis_url, socket_connect_timeout=20, decode_responses=True)
    r.ping()

    pg = survey_pg(conn, wa)
    rd = survey_redis(r, wa)
    print(f"\nHistorial de wa_id={wa}:")
    for t, n in pg.items():
        print(f"  Postgres {t:<24} {'(no existe)' if n is None else f'{n} filas'}")
    for p, keys in rd.items():
        print(f"  Redis    {p:<24} {len(keys)} claves")
    total = sum(n for n in pg.values() if n) + sum(len(k) for k in rd.values())

    if not args.apply:
        print(f"\n[DRY-RUN] Total a borrar para {wa}: {total}. No se borró nada.")
        return
    if total == 0:
        print(f"\nNada que borrar para {wa}. ✅")
        return
    if not args.yes:
        ans = input(f"\n¿Borrar {total} elementos del wa_id {wa} en PRODUCCIÓN? (escribe 'si'): ").strip().lower()
        if ans not in ("si", "sí", "yes", "y"):
            print("Cancelado.")
            return

    dpg = wipe_pg(conn, wa)
    drd = wipe_redis(r, rd)
    print("\nBorrado:")
    for t, n in dpg.items():
        if n is not None:
            print(f"  Postgres {t}: {n} filas")
    for p, n in drd.items():
        print(f"  Redis {p}: {n} claves")

    left = sum(n for n in survey_pg(conn, wa).values() if n) + sum(
        len(k) for k in survey_redis(r, wa).values()
    )
    conn.close()
    if left == 0:
        print(f"\n✅ Verificado: no queda historial del wa_id {wa}.")
    else:
        print(f"\n⚠️  Quedaron {left} elementos del wa_id {wa}.")
        sys.exit(2)


if __name__ == "__main__":
    main()
