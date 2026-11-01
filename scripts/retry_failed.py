"""Explicitly requeue failed articles after correcting the model configuration."""
import sqlite3
from pathlib import Path
p=Path(__file__).resolve().parents[1]/'data/live.sqlite3'
with sqlite3.connect(str(p)) as db:
    count=db.execute("UPDATE articles SET model_status='pending',model_error=NULL WHERE model_status='failed'").rowcount
print('Requeued failed articles:',count)
