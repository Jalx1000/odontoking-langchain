#!/usr/bin/env python
"""Vacía los DATOS de TODAS las tablas del esquema public (TRUNCATE), no borra el esquema.

Para dejar la base limpia entre pruebas: trunca cada tabla de `public` con
RESTART IDENTITY CASCADE, así los ids vuelven a empezar y no quedan referencias colgando.

La URL se pasa por variable de entorno (NUNCA por argumento, para que no quede en el
historial del shell):

    DB_TRUNCATE_URL   URL de Postgres (pública/alcanzable desde donde corres esto)

Excluye por defecto `alembic_version` (si la truncas, Alembic cree que no hay migraciones
aplicadas y el próximo `make migrate` intenta re-crear todo). Ajusta con KEEP="a,b,c".

Uso (vía Makefile: `make db-wipe-all` / `make db-wipe-all CONFIRM=yes`):
    python scripts/db_truncate_all.py            # dry-run: lista tablas y filas, no borra
    python scripts/db_truncate_all.py --apply    # trunca (pide confirmación)
    python scripts/db_truncate_all.py --apply --yes   # sin pregunta interactiva
"""

import argparse
import os
import sys

import psycopg
from psycopg import sql

# Tablas que NO se truncan por defecto: estructurales para que la app siga migrada/operativa.
DEFAULT_KEEP = {"alembic_version"}


def _die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def _all_public_tables(conn: psycopg.Connection) -> list[str]:
    """Nombres de todas las tablas base del esquema public (sin vistas)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' "
            "ORDER BY table_name"
        )
        return [row[0] for row in cur.fetchall()]


def _count(conn: psycopg.Connection, table: str) -> int:
    with conn.cursor() as cur:
        cur.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table)))
        row = cur.fetchone()
        return int(row[0]) if row else 0


def main() -> None:
    """Survey (o TRUNCATE con --apply) de todas las tablas de public salvo las de KEEP."""
    ap = argparse.ArgumentParser(description="TRUNCATE de todas las tablas de public.")
    ap.add_argument("--apply", action="store_true", help="Ejecuta el TRUNCATE (sin esto, dry-run).")
    ap.add_argument("--yes", action="store_true", help="No preguntar confirmación.")
    args = ap.parse_args()

    db_url = os.getenv("DB_TRUNCATE_URL")
    if not db_url:
        _die("Falta DB_TRUNCATE_URL (URL de Postgres alcanzable).")

    keep_env = os.getenv("KEEP", "")
    keep = {t.strip() for t in keep_env.split(",") if t.strip()} or set(DEFAULT_KEEP)

    try:
        conn = psycopg.connect(db_url, connect_timeout=15)
    except Exception as e:  # noqa: BLE001
        _die(f"No pude conectar a Postgres: {e}")

    tables = _all_public_tables(conn)
    targets = [t for t in tables if t not in keep]

    print(f"Esquema public: {len(tables)} tablas. Se mantienen (KEEP): {sorted(keep)}")
    print("Tablas a truncar y filas actuales:")
    total = 0
    for t in targets:
        n = _count(conn, t)
        total += n
        print(f"  {t}: {n}")

    if not args.apply:
        print(f"\n[DRY-RUN] Total de filas que se borrarían: {total}. No se borró nada.")
        print("Para borrar: make db-wipe-all CONFIRM=yes")
        conn.close()
        return

    if not targets:
        print("\nNo hay tablas para truncar. ✅")
        conn.close()
        return

    if not args.yes:
        ans = input(f"\n¿Truncar {len(targets)} tablas ({total} filas) en esta base? (escribe 'si'): ").strip().lower()
        if ans not in ("si", "sí", "yes", "y"):
            print("Cancelado. No se borró nada.")
            conn.close()
            return

    # Un solo TRUNCATE con todas las tablas: RESTART IDENTITY reinicia las secuencias,
    # CASCADE limpia las FKs entre ellas sin pelear con el orden.
    stmt = sql.SQL("TRUNCATE TABLE {} RESTART IDENTITY CASCADE").format(
        sql.SQL(", ").join(sql.Identifier(t) for t in targets)
    )
    with conn.cursor() as cur:
        cur.execute(stmt)
    conn.commit()

    left = sum(_count(conn, t) for t in targets)
    conn.close()
    if left == 0:
        print(f"\n✅ Truncadas {len(targets)} tablas. Todo en 0.")
    else:
        print(f"\n⚠️  Quedaron {left} filas. Revisa la salida.")
        sys.exit(2)


if __name__ == "__main__":
    main()
