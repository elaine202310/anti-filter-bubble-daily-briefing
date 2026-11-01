import json
import unittest
from briefing_pipeline.gemini import GeminiClient

class GeminiTests(unittest.TestCase):
    def test_structured_request_and_response(self):
        expected = dict(category='science',summary='Summary',importance=3,reason='Reason')
        def transport(payload):
            request=json.loads(payload)
            self.assertEqual(request['generationConfig']['responseMimeType'],'application/json')
            self.assertIn('responseJsonSchema',request['generationConfig'])
            return json.dumps({'candidates':[{'content':{'parts':[{'text':json.dumps(expected)}]}}]}).encode()
        client=GeminiClient('test-only',transport=transport,retries=0)
        self.assertEqual(client.classify(dict(id='test',title='Test',description='Text')),expected)
    def test_invalid_result_fails_without_secret(self):
        client=GeminiClient('secret-value',transport=lambda _: b'{}',retries=0)
        with self.assertRaises(RuntimeError) as result:
            client.classify(dict(id='test',title='Test',description='Text'))
        self.assertNotIn('secret-value',str(result.exception))
