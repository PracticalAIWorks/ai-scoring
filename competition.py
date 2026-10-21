"""One active competition; independent SQLite files preserve historical events."""
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
import sqlite3
import uuid

current=ContextVar('competition_id',default='default')
locked=ContextVar('competition_registry',default=None)
root=None


@contextmanager
def registry():
    if locked.get() is not None:
        yield locked.get();return
    db=sqlite3.connect(root/'competitions.sqlite3',timeout=15);db.row_factory=sqlite3.Row
    try:
        with db:yield db
    finally:db.close()


def initialize(data,title):
    global root
    root=Path(data);(root/'competitions').mkdir(exist_ok=True)
    with registry() as db:
        db.execute('CREATE TABLE IF NOT EXISTS competitions(id TEXT PRIMARY KEY,title TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS control(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
        db.execute('INSERT OR IGNORE INTO competitions VALUES (?,?)',('default',title))
        db.execute("INSERT OR IGNORE INTO control VALUES ('active','default')")


def active():
    with registry() as db:return db.execute("SELECT value FROM control WHERE key='active'").fetchone()[0]


def listing():
    with registry() as db:return [dict(r) for r in db.execute('SELECT id,title FROM competitions ORDER BY rowid')]


def database(original):
    return original if current.get()=='default' else root/'competitions'/(current.get()+'.sqlite3')


def activate(identifier):
    with registry() as db:
        if not db.execute('SELECT 1 FROM competitions WHERE id=?',(identifier,)).fetchone():raise ValueError('比赛不存在')
        db.execute("UPDATE control SET value=? WHERE key='active'",(identifier,))


def register(identifier,title):
    with registry() as db:db.execute('INSERT INTO competitions VALUES (?,?)',(identifier,title))


def rename(title):
    with registry() as db:db.execute('UPDATE competitions SET title=? WHERE id=?',(title,current.get()))


def identifier():return uuid.uuid4().hex


@contextmanager
def mutation():
    with registry() as db:
        db.execute('BEGIN IMMEDIATE');token=locked.set(db)
        try:yield
        finally:locked.reset(token)
