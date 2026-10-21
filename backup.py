"""python backup.py <backup-directory>; uses SQLite's online backup API."""
import os
from pathlib import Path
import shutil
import sqlite3
import sys
from datetime import datetime

if __name__ == '__main__':
    source = Path(os.environ.get('DATA_DIR', Path(__file__).resolve().parent / 'data'))
    dest = Path(sys.argv[1]) / datetime.now().strftime('%Y%m%d-%H%M%S')
    if not (source / 'scores.sqlite3').is_file():
        raise SystemExit('Database not found. Set DATA_DIR to the application data directory.')
    dest.mkdir(parents=True, exist_ok=False)
    with sqlite3.connect(source / 'scores.sqlite3') as src, sqlite3.connect(dest / 'scores.sqlite3') as out:
        src.backup(out)
    if (source/'competitions.sqlite3').is_file():
        with sqlite3.connect(source/'competitions.sqlite3') as src,sqlite3.connect(dest/'competitions.sqlite3') as out:src.backup(out)
        with sqlite3.connect(dest/'competitions.sqlite3') as registry:
            identifiers=[row[0] for row in registry.execute("SELECT id FROM competitions WHERE id!='default'")]
        (dest/'competitions').mkdir(exist_ok=True)
        for identifier in identifiers:
            file=source/'competitions'/(identifier+'.sqlite3')
            if not file.is_file():raise SystemExit('Competition database missing; backup incomplete: '+identifier)
            with sqlite3.connect(file) as src,sqlite3.connect(dest/'competitions'/file.name) as out:src.backup(out)
    if (source / 'media').is_dir():
        shutil.copytree(source / 'media', dest / 'media')
    if (source / 'wecom-verification').is_dir():
        shutil.copytree(source / 'wecom-verification', dest / 'wecom-verification')
    print(f'Backup saved: {dest.resolve()}')
