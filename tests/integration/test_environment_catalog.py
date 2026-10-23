"""Environment settings must be executable, isolated and round-trip through the UI."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from web.backend.ai_settings import AISettings, Credentials, defaults
from web.backend.config import _read_env, write_env, EnvironmentStore
from web.backend.runtime_config import CATALOG, get, template, validate


class Keys:
    def get(self, config): return ''
    def set(self, config, value): pass
    def delete(self, config): pass


class EnvironmentCatalogTests(unittest.TestCase):
    def test_example_covers_every_declared_option_and_contains_no_secrets(self):
        values = _read_env(Path('.env.example'))
        self.assertEqual(set(values),set(CATALOG))
        self.assertEqual(values['LECORDER_SPEECH_API_KEY'],'')
        self.assertEqual(values['LECORDER_LLM_API_KEY'],'')
        with patch.dict(os.environ,values): validate()

    def test_defaults_use_env_paths_and_live_model_but_reset_is_factory_default(self):
        with patch.dict(os.environ,{'LECORDER_LLM_MODEL':'custom-llm','LECORDER_LIVE_MODEL':'custom-live',
                                   'LECORDER_LLM_MODEL_PATH':'models/custom.gguf','WHISPER_IDLE_SECONDS':'12'}):
            config=defaults()
            self.assertEqual(config['llm']['model'],'custom-llm')
            self.assertEqual(config['speech']['live_model'],'custom-live')
            self.assertEqual(config['speech']['idle_seconds'],12)
            self.assertTrue(Path(config['llm']['model_path']).is_absolute())
            self.assertEqual(defaults(environment=False)['llm']['model'],'Qwen3.5-9B')

    def test_ui_save_updates_env_preserves_categories_and_active_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);env=root/'.env';env.write_text('# LLM category\nLECORDER_LLM_MODEL=old\n# Advanced category\nLECORDER_OVERVIEW_TOKENS=1536\n')
            store=AISettings(root/'ai.json',Keys(),env_path=env)
            # Use a custom server to avoid touching local processes and real files.
            store.save('llm',{'provider':'llama_cpp','managed':False,'model':'before','base_url':'http://127.0.0.1:18081'})
            store.freeze()
            store.save('llm',{'provider':'llama_cpp','managed':False,'model':'after','base_url':'http://127.0.0.1:18081'})
            self.assertEqual(store.snapshot()['llm']['model'],'before')
            self.assertEqual(store.saved_snapshot()['llm']['model'],'after')
            self.assertTrue(store.public()['restart_required'])
            self.assertEqual(_read_env(env)['LECORDER_LLM_MODEL'],'after')
            self.assertIn('# Advanced category',env.read_text())
            self.assertIn('# LLM category',env.read_text())
            # Editing the file directly is also reflected in saved settings.
            write_env(env,{'LECORDER_LLM_MODEL':'manual'})
            self.assertEqual(store.saved_snapshot()['llm']['model'],'manual')
            isolated=AISettings(root/'isolated.json',Keys())
            self.assertNotEqual(isolated.saved_snapshot()['llm']['model'],'manual')

    def test_save_api_runs_with_environment_backed_settings(self):
        from flask import Flask
        from web.backend.ai_routes import ai_routes
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);env=root/'.env';env.write_text('# Keep categories\n')
            settings=AISettings(root/'ai.json',Keys(),env_path=env)
            app=Flask(__name__);app.register_blueprint(ai_routes(settings));client=app.test_client()
            payload={'config':{'provider':'llama_cpp','managed':False,'model':'test-env-model','base_url':'http://127.0.0.1:18081'}}
            with patch('web.backend.ai_routes.check_connection',return_value={}):
                self.assertEqual(client.post('/api/ai-settings/llm/check',json=payload).status_code,200)
            self.assertEqual(client.put('/api/ai-settings/llm',json=payload).status_code,200)
            self.assertEqual(_read_env(env)['LECORDER_LLM_MODEL'],'test-env-model')
            self.assertEqual(client.get('/api/ai-settings').json['settings']['llm']['model'],'test-env-model')

    def test_empty_model_path_uses_install_default_instead_of_old_json(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);env=root/'.env';env.write_text("LECORDER_LLM_MODEL_PATH=''\n")
            store=AISettings(root/'ai.json',Keys(),env_path=env)
            old=defaults();old['llm']['model_path']='/previous/computer/model.gguf'
            store.path.write_text(json.dumps({'settings':old}))
            self.assertEqual(store.saved_snapshot()['llm']['model_path'],defaults(environment=False)['llm']['model_path'])

    def test_env_write_handles_spaces_quotes_and_comments(self):
        with tempfile.TemporaryDirectory() as folder:
            env=Path(folder)/'.env';env.write_text('# Paths\nLECORDER_OUTPUT_DIR=old\n# Keep me\nEXTRA=present\n')
            value="/tmp/folder with 'quotes' and #hash"
            write_env(env,{'LECORDER_OUTPUT_DIR':value})
            self.assertEqual(_read_env(env)['LECORDER_OUTPUT_DIR'],value)
            self.assertIn('# Keep me',env.read_text())
            self.assertEqual(_read_env(env)['EXTRA'],'present')

    def test_invalid_values_fail_with_setting_name_and_cross_field_validation(self):
        with patch.dict(os.environ,{'LECORDER_LLM_BATCH_SIZE':'oops'}):
            with self.assertRaisesRegex(ValueError,'LECORDER_LLM_BATCH_SIZE'):get('LECORDER_LLM_BATCH_SIZE')
        with patch.dict(os.environ,{'LECORDER_LIVE_MIN_SECONDS':'15','LECORDER_LIVE_MAX_SECONDS':'10'}):
            with self.assertRaisesRegex(ValueError,'최소 발화'):validate()

    def test_short_configured_chunks_advance_with_or_without_quiet_points(self):
        from web.backend.speech.speech_processing import SpeechProcessing
        with patch.dict(os.environ,{'LECORDER_SPEECH_CHUNK_SECONDS':'20','LECORDER_SPEECH_CUT_SEARCH_SECONDS':'0'}):
            for points in ([],[0,20,40,60]):
                with patch('web.backend.speech.speech_processing.media_request',return_value={'points':points}):
                    boundaries=SpeechProcessing._chunk_boundaries(Path('not-read.wav'),65)
                self.assertEqual([b[0] for b in boundaries],[0,20,40,60,65])
                self.assertTrue(all(a[0]<b[0] for a,b in zip(boundaries,boundaries[1:])))

    def test_env_secrets_are_not_copied_to_public_settings_or_json(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);env=root/'.env';env.write_text('LECORDER_LLM_API_KEY=test-only-secret\n')
            keys=Credentials()
            store=AISettings(root/'ai.json',keys,env_path=env)
            config={'provider':'openai','managed':False,'model':'test-model','base_url':'https://example.test'}
            store.save('llm',config)
            public=store.public()
            self.assertTrue(public['settings']['llm']['key_present'])
            self.assertNotIn('test-only-secret',json.dumps(public))
            self.assertNotIn('test-only-secret',store.path.read_text())
            self.assertEqual(keys.get(store.saved_snapshot()['llm']),'test-only-secret')
