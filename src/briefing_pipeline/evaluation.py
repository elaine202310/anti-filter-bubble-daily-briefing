"""Evaluation metrics for a manually annotated CSV dataset."""
import csv
from pathlib import Path
from typing import Dict, List

def metrics(labels: List[int], predictions: List[int]) -> Dict[str, float]:
    if len(labels) != len(predictions) or not labels:
        raise ValueError("labels and predictions must have equal nonzero length")
    if any(type(x) is not int or not 1 <= x <= 5 for x in labels + predictions):
        raise ValueError("scores must be integers from 1 to 5")
    n = len(labels)
    return {"n": n, "exact_agreement": sum(a == b for a, b in zip(labels, predictions)) / n,
            "within_1_point": sum(abs(a-b) <= 1 for a, b in zip(labels, predictions)) / n}

def evaluate_csv(path: Path) -> Dict[str, float]:
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if len(rows) != 30:
        raise ValueError("evaluation requires exactly 30 annotated rows; found %d" % len(rows))
    return metrics([int(r["human_importance"]) for r in rows], [int(r["model_importance"]) for r in rows])


def evaluate_labels(path, database):
    """Join blind human labels with persisted real model results by article ID."""
    import sqlite3
    import hashlib
    with path.open(newline='',encoding='utf-8') as f:
        rows=list(csv.DictReader(f))
    if len(rows)!=30 or len({r['article_id'] for r in rows})!=30:
        raise ValueError('Exactly 30 unique article IDs required')
    labels=[];predictions=[];provenance=set();details=[]
    db=sqlite3.connect(str(database));db.row_factory=sqlite3.Row
    try:
        for row in rows:
            if row['human_importance'] not in ('1','2','3','4','5'):
                raise ValueError('All 30 human labels must be filled with 1..5')
            prediction=db.execute("SELECT * FROM articles WHERE id=? AND model_status='complete' AND provider IS NOT NULL",(row['article_id'],)).fetchone()
            if prediction is None:
                raise ValueError('Missing real model prediction for '+row['article_id'])
            labels.append(int(row['human_importance']));predictions.append(prediction['importance'])
            provenance.add((prediction['provider'],prediction['model']))
            details.append({'article_id':row['article_id'],'human':labels[-1],'model':predictions[-1]})
    finally:db.close()
    if len(provenance)!=1:
        raise ValueError('Evaluation must use one provider/model combination')
    result=metrics(labels,predictions)
    result.update(provider=next(iter(provenance))[0],model=next(iter(provenance))[1],labels_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),items=details)
    return result
