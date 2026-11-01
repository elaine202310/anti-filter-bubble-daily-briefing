"""Live execution with per-run evidence, provenance, bounded work and locking."""
import csv
import fcntl
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from .core import Store, QwenClient, load_env, parse_feed, rank_interest, rank_diverse, render_digest, deliver_email
from .gemini import GeminiClient
from .preferences import load_preferences


def initialize(store):
    columns = {r[1] for r in store.db.execute('PRAGMA table_info(articles)')}
    for name in ('provider', 'model', 'processed_at'):
        if name not in columns:
            store.db.execute('ALTER TABLE articles ADD COLUMN ' + name + ' TEXT')
    store.db.execute('CREATE TABLE IF NOT EXISTS deliveries (day TEXT PRIMARY KEY, status TEXT, detail TEXT)')
    store.db.commit()


def run(root, collect_only=False):
    from .cli import fetch, setup_logging
    for folder in ('data','logs','output'):
        (root/folder).mkdir(parents=True,exist_ok=True)
    setup_logging(root/'logs/pipeline.log')
    lock=(root/'data/run.lock').open('w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise RuntimeError('Another run is already active')
    store=Store(root/'data/live.sqlite3')
    try:
        initialize(store)
        env=load_env(root/'.env')
        sources=json.loads((root/'sources.json').read_text())
        if len(sources)!=6 or len({x['url'] for x in sources})!=6:
            raise ValueError('Exactly six distinct RSS source URLs required')
        report={'started_at':datetime.now(timezone.utc).isoformat(),'feeds':[], 'processed':0,'failed':0,'email':'not_attempted'}
        for source in sources:
            entry={'source':source['name'],'url':source['url']}
            try:
                articles=parse_feed(fetch(source['url'],timeout=12),source['name'])
                entry.update(status='ok',parsed=len(articles),inserted=sum(store.add(a) for a in articles))
            except Exception as exc:
                entry.update(status='failed',error=type(exc).__name__)
            report['feeds'].append(entry)
            logging.info('feed result %s',entry)
        if not collect_only:
            provider=env.get('LLM_PROVIDER','gemini')
            if provider not in ('gemini','qwen'):
                raise ValueError('Unknown provider')
            model=env.get('GEMINI_MODEL','gemini-3.5-flash-lite') if provider=='gemini' else env.get('QWEN_MODEL','qwen-plus')
            key=env.get('GEMINI_API_KEY' if provider=='gemini' else 'QWEN_API_KEY','')
            report.update(provider=provider,model=model)
            if key:
                client=GeminiClient(key,model,timeout=40,retries=1) if provider=='gemini' else QwenClient(key,model,env['QWEN_BASE_URL'])
                limit=int(env.get('MAX_ARTICLES_PER_RUN','3'))
                if not 1<=limit<=1000:
                    raise ValueError('MAX_ARTICLES_PER_RUN must be 1..1000')
                cutoff=(datetime.now(timezone.utc)-timedelta(hours=int(env.get('LOOKBACK_HOURS','48')))).isoformat()
                pending=list(store.db.execute("SELECT * FROM articles WHERE model_status='pending' AND published_at>=? AND published_at<=? ORDER BY published_at DESC LIMIT ?",(cutoff,datetime.now(timezone.utc).isoformat(),limit)))
                for index,row in enumerate(pending):
                    if index:
                        time.sleep(float(env.get('REQUEST_INTERVAL_SECONDS','4')))
                    try:
                        result=client.classify(dict(row));store.save_model(row['id'],result)
                        store.db.execute('UPDATE articles SET provider=?,model=?,processed_at=? WHERE id=?',(provider,model,datetime.now(timezone.utc).isoformat(),row['id']));store.db.commit()
                        report['processed']+=1
                    except Exception as exc:
                        store.fail_model(row['id'],str(exc));report['failed']+=1
                        if any(x in str(exc) for x in ('HTTP 400','HTTP 401','HTTP 403','HTTP 429','timeout')):
                            report['model_stopped']=str(exc);break
            else:
                report['model_stopped']='API key missing'
            create_digest(root,store,env,report)
        report['finished_at']=datetime.now(timezone.utc).isoformat()
        destination=root/'output'/('collect_report.json' if collect_only else 'run_report.json')
        destination.write_text(json.dumps(report,indent=2))
        logging.info('run report saved %s',destination)
        print(json.dumps(report,indent=2))
        return 0 if collect_only or report.get('digest_size', 0) > 0 else 2
    finally:
        store.close();lock.close()


def create_digest(root,store,env,report):
    cutoff=(datetime.now(timezone.utc)-timedelta(hours=int(env.get('LOOKBACK_HOURS','48')))).isoformat()
    rows=[dict(r) for r in store.db.execute("SELECT * FROM articles WHERE model_status='complete' AND provider IS NOT NULL AND published_at>=? AND published_at<=?",(cutoff,datetime.now(timezone.utc).isoformat()))]
    preferred=load_preferences(root,env)
    size=int(env.get('DIGEST_SIZE','10'))
    baseline=rank_interest(rows,preferred,size);diverse=rank_diverse(rows,preferred,size)
    comparison={'candidate_count':len(rows),'actual_size':len(diverse),'quota':0.2,'preferred_categories':preferred}
    for name,selection in [('baseline',baseline),('diverse',diverse)]:
        count=sum(r['category'] not in preferred for r in selection)
        comparison[name]={'ids':[r['id'] for r in selection],'outside_count':count,'outside_share':count/len(selection) if selection else None}
    (root/'output/ranking_comparison.json').write_text(json.dumps(comparison,indent=2))
    (root/'output/digest_items.json').write_text(json.dumps(diverse,indent=2))
    body=render_digest(diverse)
    (root/'output/digest.md').write_text(body)
    report['digest_size']=len(diverse)
    if not diverse:
        report['email']='skipped_empty_digest';return
    # Preview is always preserved. Sending requires the separate explicit command.
    report['email']=deliver_email(body,dict(env,EMAIL_PREVIEW_ONLY='true'),root/'output/email_preview.txt')


def send(root):
    env=load_env(root/'.env')
    for key in ('SMTP_HOST','SMTP_FROM','SMTP_TO','SMTP_USERNAME','SMTP_PASSWORD'):
        if not env.get(key):
            raise ValueError(key+' missing; run python3 scripts/configure_gmail.py locally')
    items=json.loads((root/'output/digest_items.json').read_text())
    if not items or any(not r.get('provider') for r in items):
        raise ValueError('A nonempty real-model digest is required')
    if any(datetime.fromisoformat(r['published_at']) < datetime.now(timezone.utc)-timedelta(hours=48) for r in items):
        raise ValueError('Digest is stale; run pipeline again')
    store=Store(root/'data/live.sqlite3');initialize(store)
    day=datetime.now().date().isoformat()
    try:
        if store.db.execute('SELECT 1 FROM deliveries WHERE day=?',(day,)).fetchone():
            raise ValueError('Delivery already attempted today; inspect database before resending')
        store.db.execute('INSERT INTO deliveries VALUES(?,?,?)',(day,'sending','SMTP acknowledgement pending'));store.db.commit()
        try:
            deliver_email(render_digest(items),dict(env,EMAIL_PREVIEW_ONLY='false'),root/'output/email_preview.txt')
        except Exception:
            store.db.execute('UPDATE deliveries SET status=?,detail=? WHERE day=?',('uncertain','Check mailbox before retrying',day));store.db.commit();raise
        store.db.execute('UPDATE deliveries SET status=?,detail=? WHERE day=?',('accepted','SMTP server accepted message; inbox receipt not verified',day));store.db.commit()
        print('SMTP server accepted the digest. Inbox receipt needs confirmation.')
    finally:
        store.close()
    return 0


def export_labels(root):
    store=Store(root/'data/live.sqlite3');initialize(store)
    try:
        rows=list(store.db.execute("SELECT * FROM articles WHERE model_status='complete' AND provider IS NOT NULL ORDER BY id LIMIT 30"))
        if len(rows)!=30:
            raise ValueError('Need 30 real model predictions; continue run until 30 are complete')
        out=root/'output/human_labels.csv'
        if out.exists():
            with out.open(newline='',encoding='utf-8') as existing:
                if any(row.get('human_importance','').strip() for row in csv.DictReader(existing)):
                    raise ValueError('Existing human labels must not be overwritten')
        with out.open('w',newline='',encoding='utf-8') as f:
            writer=csv.writer(f);writer.writerow(['article_id','title','url','description','human_importance','notes'])
            for r in rows:writer.writerow([r['id'],r['title'],r['url'],r['description'],'',''])
        print('Exported 30 articles for blind human labeling:',out)
    finally:store.close()
    return 0
