import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from unittest.mock import patch
from briefing_pipeline.pipeline import run
from briefing_pipeline.evaluation import evaluate_labels

class LivePipelineTests(unittest.TestCase):
    def test_six_feeds_partial_failure_model_and_preview(self):
        published = format_datetime(datetime.now(timezone.utc))
        feed = ('<rss><channel><item><title>Science study</title><link>https://example.org/article?utm_source=abc</link><description>A new study</description><pubDate>'+published+'</pubDate></item></channel></rss>').encode()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'sources.json').write_text(json.dumps([{'name':str(i),'url':'https://example.org/'+str(i)} for i in range(6)]))
            (root/'.env').write_text('GEMINI_API_KEY=test\nMAX_ARTICLES_PER_RUN=1\nEMAIL_PREVIEW_ONLY=true\n')
            response={'category':'science','summary':'Research summary','importance':3,'reason':'Relevant study'}
            body=json.dumps({'candidates':[{'content':{'parts':[{'text':json.dumps(response)}]}}]}).encode()
            with patch('briefing_pipeline.cli.fetch',side_effect=[feed,feed,feed,feed,feed,ValueError('bad feed')]), patch('briefing_pipeline.gemini.GeminiClient._unused',create=True), patch('briefing_pipeline.pipeline.GeminiClient') as client:
                client.return_value.classify.return_value=response
                self.assertEqual(run(root),0)
            report=json.loads((root/'output/run_report.json').read_text())
            self.assertEqual(report['processed'],1)
            self.assertEqual(report['feeds'][-1]['status'],'failed')
            self.assertEqual(report['email'],'preview')
            with sqlite3.connect(str(root/'data/live.sqlite3')) as db:
                self.assertEqual(db.execute('select count(*) from articles').fetchone()[0],1)

    def test_evaluation_requires_real_prediction_for_each_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'labels.csv'
            path.write_text('article_id,human_importance\n'+'\n'.join('%d,3'%i for i in range(30)))
            db=Path(tmp)/'db.sqlite3';sqlite3.connect(str(db)).close()
            with self.assertRaises((sqlite3.OperationalError,ValueError)):
                evaluate_labels(path,db)
