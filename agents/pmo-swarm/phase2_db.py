"""Phase 2: Create isrds_agentic database and run all 4 migrations."""
import io, sys, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import psycopg2
from psycopg2 import sql
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
from pathlib import Path

HOST     = "34.16.10.122"
PORT     = 5432
USER     = "postgres"
PASSWORD = os.environ.get("ALLOYDB_PASSWORD", "")
MAIN_DB  = "isrd_db"
AGENT_DB = "isrds_agentic"

MIGRATIONS_DIR = Path(__file__).parent.parent.parent.parent / "migrations"

print("[Phase 2] Database setup")
print(f"  Host:    {HOST}:{PORT}")
print(f"  Main DB: {MAIN_DB}")
print(f"  New DB:  {AGENT_DB}")
print(f"  Migrations: {MIGRATIONS_DIR}")

# ── Step 1: Create isrds_agentic database ─────────────────────────────────────
print("\n[1] Create isrds_agentic database...")
try:
    conn = psycopg2.connect(host=HOST, port=PORT, user=USER, password=PASSWORD, dbname=MAIN_DB)
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (AGENT_DB,))
    if cur.fetchone():
        print(f"  Database '{AGENT_DB}' already exists")
    else:
        cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(AGENT_DB)))
        print(f"  Created database: {AGENT_DB}")
    cur.close()
    conn.close()
except Exception as e:
    print(f"  ERROR creating database: {e}")
    sys.exit(1)

# ── Step 2: Check pgvector availability ──────────────────────────────────────
print("\n[2] Check pgvector extension...")
try:
    conn = psycopg2.connect(host=HOST, port=PORT, user=USER, password=PASSWORD, dbname=AGENT_DB)
    cur = conn.cursor()
    cur.execute("SELECT * FROM pg_available_extensions WHERE name = 'vector'")
    row = cur.fetchone()
    if row:
        print(f"  pgvector available: version {row[1] or 'unknown'}")
    else:
        print("  WARNING: pgvector extension not available on this server")
        print("  agent_memory vector search will be disabled")
    cur.close()
    conn.close()
except Exception as e:
    print(f"  ERROR checking pgvector: {e}")

# ── Step 3: Run migrations ────────────────────────────────────────────────────
MIGRATION_FILES = [
    "001_foundation.sql",
    "002_survey.sql",
    "003_seed_config_registry.sql",
    "004_pmo_swarm.sql",
]

print("\n[3] Running migrations...")
for fname in MIGRATION_FILES:
    fpath = MIGRATIONS_DIR / fname
    if not fpath.exists():
        print(f"  MISSING: {fpath}")
        continue
    sql_text = fpath.read_text(encoding="utf-8")
    try:
        conn = psycopg2.connect(host=HOST, port=PORT, user=USER, password=PASSWORD, dbname=AGENT_DB)
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cur = conn.cursor()
        cur.execute(sql_text)
        conn.commit()
        cur.close()
        conn.close()
        print(f"  OK  {fname}")
    except Exception as e:
        print(f"  ERR {fname}: {str(e)[:200]}")

# ── Step 4: Verify tables created ────────────────────────────────────────────
print("\n[4] Verifying tables...")
try:
    conn = psycopg2.connect(host=HOST, port=PORT, user=USER, password=PASSWORD, dbname=AGENT_DB)
    cur = conn.cursor()
    cur.execute("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public' ORDER BY table_name
    """)
    tables = [r[0] for r in cur.fetchall()]
    for t in tables:
        print(f"  table: {t}")
    cur.close()
    conn.close()
    print(f"\n  {len(tables)} tables created")
except Exception as e:
    print(f"  ERROR verifying tables: {e}")

# ── Step 5: Verify PMO seed data ──────────────────────────────────────────────
print("\n[5] Verify seed data...")
try:
    conn = psycopg2.connect(host=HOST, port=PORT, user=USER, password=PASSWORD, dbname=AGENT_DB)
    cur = conn.cursor()
    cur.execute("SELECT role_category, engine_binding FROM config_registry ORDER BY role_category")
    rows = cur.fetchall()
    for r in rows:
        print(f"  role: {r[0]}  engine: {r[1]}")
    cur.close()
    conn.close()
except Exception as e:
    print(f"  ERROR reading config_registry: {e}")

print("\n[Phase 2 complete]")
