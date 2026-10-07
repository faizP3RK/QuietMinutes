"""Local voiceprint database (SQLite, stdlib) — names → embedding vectors.

Implicit enrollment: when the user renames a diarized cluster to a real name, that
cluster's embedding is saved here. Future meetings match new clusters against the DB
(cosine similarity) and suggest known names. Tiny numeric vectors only — never audio.
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager

import numpy as np

from ..config import data_dir

DB_PATH = data_dir() / "voiceprints.db"


@contextmanager
def _db():
    """Yield a connection that commits on success and always closes (no leaked handles)."""
    c = sqlite3.connect(str(DB_PATH))
    c.execute("""CREATE TABLE IF NOT EXISTS people(
        id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL,
        meetings_seen INTEGER DEFAULT 0, created TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS embeddings(
        id INTEGER PRIMARY KEY, person_id INTEGER NOT NULL,
        vec BLOB NOT NULL, dim INTEGER NOT NULL, created TEXT,
        FOREIGN KEY(person_id) REFERENCES people(id) ON DELETE CASCADE)""")
    try:
        yield c
        c.commit()
    finally:
        c.close()


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M")


def add_embedding(name: str, vec: np.ndarray) -> None:
    """Enroll/reinforce a person with one embedding (implicit enrollment)."""
    name = name.strip()
    if not name or vec is None:
        return
    vec = np.asarray(vec, dtype=np.float32)
    with _db() as c:
        cur = c.execute("SELECT id FROM people WHERE name=?", (name,))
        row = cur.fetchone()
        if row:
            pid = row[0]
            c.execute("UPDATE people SET meetings_seen=meetings_seen+1 WHERE id=?", (pid,))
        else:
            pid = c.execute("INSERT INTO people(name, meetings_seen, created) VALUES(?,1,?)",
                            (name, _now())).lastrowid
        c.execute("INSERT INTO embeddings(person_id, vec, dim, created) VALUES(?,?,?,?)",
                  (pid, vec.tobytes(), int(vec.shape[0]), _now()))


def match(vec: np.ndarray, threshold: float) -> tuple[str | None, float]:
    """Best (name, score) by cosine similarity, or (None, best_score) if below threshold."""
    if vec is None:
        return (None, 0.0)
    vec = np.asarray(vec, dtype=np.float32)
    best_name, best_score = None, 0.0
    with _db() as c:
        for name, blob in c.execute(
                "SELECT p.name, e.vec FROM embeddings e JOIN people p ON e.person_id=p.id"):
            other = np.frombuffer(blob, dtype=np.float32)
            if other.shape == vec.shape:
                score = float(np.dot(vec, other))
                if score > best_score:
                    best_name, best_score = name, score
    return (best_name if best_score >= threshold else None, best_score)


def list_people() -> list[dict]:
    with _db() as c:
        rows = c.execute("""SELECT p.name, p.meetings_seen, COUNT(e.id)
            FROM people p LEFT JOIN embeddings e ON e.person_id=p.id
            GROUP BY p.id ORDER BY p.name COLLATE NOCASE""").fetchall()
    return [{"name": n, "meetings_seen": m, "samples": s} for n, m, s in rows]


def delete_person(name: str) -> None:
    with _db() as c:
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("DELETE FROM people WHERE name=?", (name,))


def rename_person(old: str, new: str) -> None:
    new = new.strip()
    if not new:
        return
    with _db() as c:
        c.execute("UPDATE OR IGNORE people SET name=? WHERE name=?", (new, old))
