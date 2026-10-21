"""Provider isolation, wire formats, secret redaction and settings transactions."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from flask import Flask
from web.backend.ai_settings import AISettings, defaults, current, using
from web.backend.ai_routes import ai_routes
from web.backend.ai_providers import generate
from web.backend.ai_transport import request_json, ProviderError
from web.backend.engine import Transcriber
from web.backend.transcription import Options, CancellationToken, TranscriptionCancelled


class Keys:
    def __init__(self): self.values = {}
    def get(self, config): return self.values.get(config['provider'], '')
    def set(self, config, value): self.values[config['provider']] = value
    def delete(self, config): self.values.pop(config['provider'], None)


class AIProviderTests(unittest.TestCase):
    schema = {'type':'object','properties':{'answer':{'type':'string'}},'required':['answer'],'additionalProperties':False}

    def test_custom_whisper_tokenizer_uses_secret_store_and_cancellation(self):
        config = defaults()
        config['speech'].update(managed=False, base_url='http://example.test:8080')
        token = CancellationToken()
        with using(config), patch('web.backend.speech_processing.configured_settings') as settings, patch('web.backend.speech_processing.request_json', return_value={'tokens': 3}) as request:
            settings.return_value.credentials.get.return_value = 'test-only-key'
            result = Transcriber._with_note_hint(Options(language='en', note_keywords=('electric field',)), token)
            self.assertEqual(result.recognition_hint_tokens, 3)
            self.assertIs(request.call_args.kwargs['cancellation'], token)
            self.assertEqual(request.call_args.kwargs['headers'], {'Authorization': 'Bearer test-only-key'})

    def test_tokenizer_cancellation_is_not_downgraded_to_missing_hint(self):
        with patch('web.backend.speech_processing.request_json', side_effect=TranscriptionCancelled('cancel')):
            with self.assertRaises(TranscriptionCancelled):
                Transcriber._with_note_hint(Options(language='en', note_keywords=('electric field',)), CancellationToken())

    def test_provider_wire_formats_and_missing_usage(self):
        bodies = {
            'openai': {'choices':[{'message':{'content':'{"answer":"yes"}'}}]},
            'claude': {'content':[{'type':'text','text':'{"answer":"yes"}'}]},
            'gemini': {'candidates':[{'content':{'parts':[{'text':'{"answer":"yes"}'}]}}]},
        }
        for provider, body in bodies.items():
            with self.subTest(provider=provider), patch('web.backend.ai_providers.request_json', return_value=body) as call:
                config = {'provider':provider,'model':'test-model','base_url':'https://example.invalid','managed':False}
                result = generate([{'role':'user','content':'source','images':['aGVsbG8=']}],self.schema,config=config,api_key='test-secret')
                self.assertEqual(result['result'],{'answer':'yes'})
                self.assertIsNone(result['input_tokens'])
                self.assertNotIn('test-secret',json.dumps(result))
                payload=call.call_args.args[1]
                self.assertNotIn('test-secret',json.dumps(payload))
                self.assertIn('aGVsbG8=',json.dumps(payload))
                self.assertIn('test-secret',json.dumps(call.call_args.kwargs['headers']))

    def test_local_image_requests_do_not_reuse_multimodal_prompt_cache(self):
        config=dict(defaults()['llm'],managed=False,base_url='http://example.test')
        body={'choices':[{'message':{'content':'{"answer":"yes"}'}}]}
        with patch('web.backend.ai_providers.runtime_for'),patch('web.backend.ai_providers.request_json',return_value=body) as request:
            generate([{'role':'user','content':'page','images':['aGVsbG8=']}],self.schema,config=config,api_key='')
            self.assertIs(request.call_args.args[1]['cache_prompt'],False)
            generate([{'role':'user','content':'text only'}],self.schema,config=config,api_key='')
            self.assertNotIn('cache_prompt',request.call_args.args[1])

    def test_schema_validation_rejects_malformed_response(self):
        with patch('web.backend.ai_providers.request_json', return_value={'choices':[{'message':{'content':'{"answer":3}'}}]}):
            with self.assertRaisesRegex(ValueError,'스키마'):
                generate([{'role':'user','content':'test'}],self.schema,config={'provider':'openai','model':'x','base_url':'https://example.invalid'},api_key='secret')

    def test_http_auth_is_only_in_worker_stdin_and_error_is_sanitized(self):
        with patch.object(CancellationToken, 'run', return_value=subprocess.CompletedProcess([], 0, b'{"status":401}', b'')) as run:
            with self.assertRaises(ProviderError) as error:
                request_json('https://example.invalid', {}, headers={'Authorization':'Bearer secret'})
            self.assertNotIn('secret', str(error.exception))
            self.assertNotIn('secret', ' '.join(run.call_args.args[0]))
            self.assertIn(b'Bearer secret', run.call_args.kwargs['input_data'])
            self.assertEqual(run.call_count, 1)

    def test_retry_is_bounded(self):
        with patch('web.backend.ai_transport.worker.call', return_value={'status':429}) as run:
            with self.assertRaises(ProviderError): request_json('https://example.invalid', {})
            self.assertEqual(run.call_count, 2)

    def test_cancelled_request_does_not_send(self):
        token = CancellationToken(); token.cancel()
        with patch('web.backend.ai_transport.worker.call') as run:
            with self.assertRaises(TranscriptionCancelled): request_json('https://example.invalid', {}, cancellation=token)
            run.assert_not_called()

    def test_checked_settings_and_key_never_in_saved_json_or_response(self):
        with tempfile.TemporaryDirectory() as folder:
            settings=AISettings(Path(folder)/'ai.json',Keys());app=Flask(__name__);app.register_blueprint(ai_routes(settings));client=app.test_client()
            config={'provider':'openai','model':'example','base_url':'https://api.openai.com/v1','managed':False}
            body={'config':config,'api_key':'test-secret'}
            self.assertEqual(client.put('/api/ai-settings/llm',json=body).status_code,200)
            with patch('web.backend.ai_routes.check_connection',return_value={}):
                self.assertEqual(client.post('/api/ai-settings/llm/check',json=body).status_code,200)
            saved=client.put('/api/ai-settings/llm',json=body)
            self.assertEqual(saved.status_code,200)
            self.assertNotIn('test-secret',saved.get_data(as_text=True))
            self.assertNotIn('test-secret',settings.path.read_text())
            self.assertTrue(saved.json['settings']['llm']['key_present'])
            original=settings.snapshot()
            with using(original):
                settings.save('llm',{**config,'model':'new'})
                self.assertEqual(current('llm')['model'],'example')
            self.assertEqual(client.delete('/api/ai-settings/llm/key',json={'config':config}).status_code,200)
            self.assertFalse(client.get('/api/ai-settings').json['settings']['llm']['key_present'])
            self.assertEqual(client.put('/api/ai-settings/llm',json=body,headers={'Origin':'https://evil.invalid'}).status_code,403)

    def test_settings_check_cancel_before_request_and_during_probe(self):
        import threading
        from uuid import uuid4
        with tempfile.TemporaryDirectory() as folder:
            settings=AISettings(Path(folder)/'ai.json',Keys())
            app=Flask(__name__);app.register_blueprint(ai_routes(settings))
            body={'config':{'provider':'openai','model':'example','base_url':'https://api.openai.com/v1','managed':False},'api_key':'test-secret','check_id':str(uuid4())}
            client=app.test_client()
            self.assertEqual(client.post('/api/ai-settings/llm/check/cancel',json={'check_id':body['check_id']}).status_code,200)
            with patch('web.backend.ai_routes.check_connection') as probe:
                self.assertEqual(client.post('/api/ai-settings/llm/check',json=body).status_code,400)
                probe.assert_not_called()
            started=threading.Event();finished=threading.Event();results=[]
            body['check_id']=str(uuid4())
            def slow_probe(*args,cancellation,**kwargs):
                started.set()
                for _ in range(100):
                    if cancellation.cancelled:break
                    finished.wait(.01)
                cancellation.check()
                self.fail('probe was not cancelled')
            def run():
                with app.test_client() as worker:
                    results.append(worker.post('/api/ai-settings/llm/check',json=body).status_code)
            with patch('web.backend.ai_routes.check_connection',side_effect=slow_probe):
                thread=threading.Thread(target=run);thread.start()
                self.assertTrue(started.wait(2))
                client.post('/api/ai-settings/llm/check/cancel',json={'check_id':body['check_id']})
                thread.join(2)
                self.assertFalse(thread.is_alive())
            self.assertEqual(results,[400])
            self.assertFalse(settings.path.exists())

    def test_openai_speech_requires_real_segment_timestamps(self):
        config=defaults();config['speech']={'provider':'openai','model':'whisper-1','base_url':'https://example.invalid','managed':False}
        with using(config),patch('web.backend.ai_settings.Credentials.get',return_value='secret'),patch('web.backend.speech_processing.request_json',return_value={'text':'hello','duration':2}):
            with self.assertRaisesRegex(ValueError,'타임스탬프'):
                Transcriber._openai_speech(Path('/tmp/not-uploaded.wav'),Options(language="en"),None)

    def test_openai_speech_does_not_fabricate_confidence(self):
        config=defaults();config['speech']={'provider':'openai','model':'whisper-1','base_url':'https://example.invalid','managed':False}
        with using(config),patch('web.backend.ai_settings.Credentials.get',return_value='secret'),patch('web.backend.speech_processing.request_json',return_value={'text':'hello','duration':2,'segments':[{'text':'hello','start':.2,'end':1.8}]}):
            result=Transcriber._openai_speech(Path('/tmp/not-uploaded.wav'),Options(language="en"),None)
            self.assertIsNone(result.segments[0].avg_logprob)
            self.assertEqual(result.segments[0].start,.2)

    def test_waiting_time_is_tracked_even_when_cancelled(self):
        import threading,time
        from web.backend.inference import InferenceCoordinator
        from web.backend.execution_metrics import collect
        coordinator=InferenceCoordinator();token=CancellationToken();outcome=[]
        with coordinator.lease():
            def waiter():
                with collect() as metrics:
                    try:
                        with coordinator.lease(cancellation=token):pass
                    except TranscriptionCancelled:
                        outcome.append(metrics['waiting_seconds'])
            thread=threading.Thread(target=waiter);thread.start();time.sleep(.03);token.cancel();thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertGreaterEqual(outcome[0],.03)

    def test_provider_failure_is_logged_without_request_or_credentials(self):
        from web.backend.execution_metrics import collect
        with collect() as trace,patch('web.backend.ai_providers.request_json',side_effect=ProviderError('API key authentication failed')):
            with self.assertRaises(ProviderError):
                generate([{'role':'user','content':'private source'}],self.schema,config={'provider':'openai','model':'test','base_url':'https://example.invalid'},api_key='secret')
            self.assertEqual(trace['provider_calls'][0]['status'],'failed')
            self.assertNotIn('secret',json.dumps(trace))
            self.assertNotIn('private source',json.dumps(trace))


    def test_live_model_settings_persist_independently_of_external_final_provider(self):
        from web.backend.ai_settings import live_configuration
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for name in ('turbo.bin','vad.bin'):
                (root/name).write_bytes(b'fixture')
            store=AISettings(root/'settings.json', Keys())
            config={'provider':'openai','managed':False,'base_url':'https://api.openai.com/v1','model':'whisper-1',
                    'live_base_url':'http://127.0.0.1:19082','live_model':'custom-live','live_model_path':str(root/'turbo.bin'),'live_vad_path':str(root/'vad.bin')}
            store.save('speech',config)
            saved=AISettings(store.path,Keys()).snapshot()['speech']
            live=live_configuration(saved)
            self.assertEqual(live['base_url'],'http://127.0.0.1:19082')
            self.assertEqual(live['binary'],saved['binary'])
            self.assertEqual(live['provider'],'whisper_cpp')
            self.assertTrue(live['managed'])
            self.assertEqual(live['model'],'custom-live')
            self.assertEqual(live['model_path'],str((root/'turbo.bin').absolute()))
            self.assertEqual(live['vad_path'],str((root/'vad.bin').absolute()))
            with self.assertRaisesRegex(ValueError,'파일'):
                store.save('speech',dict(config,live_vad_path=str(root/'missing.bin')))
            for url in ('https://example.com:19082', 'http://user@127.0.0.1:19082', 'http://127.0.0.1:19082/path', 'http://127.0.0.1:8081'):
                with self.assertRaises(ValueError):
                    store.save('speech', dict(config, live_base_url=url))
            self.assertEqual(store.snapshot()['speech'],saved)

if __name__=='__main__':unittest.main()
