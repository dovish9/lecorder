import tempfile
from pathlib import Path

from tests.support import RecorderCase, FakeTranscriber
from web.backend.platforms import PosixHost
from web.backend.routes import create_app
from web.backend.store import LectureStore
from web.backend.services import Services, current_services, start_thread
from web.backend.ai_settings import configured_settings


class ServiceTests(RecorderCase):
    def test_app_construction_does_not_start_queues_or_models_and_close_joins_workers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); environment,_=self.environment(root)
            services=Services.create(root/'data',platform=PosixHost())
            store=LectureStore(root/'data/app.db',environment)
            app=create_app(store=store,environment=environment,transcriber=FakeTranscriber(),services=services)
            pipeline=app.extensions['recording_pipeline']
            try:
                self.assertEqual(pipeline._threads,[])
                self.assertEqual(pipeline.notes._threads,[])
                self.assertEqual(pipeline.reviews._threads,[])
                self.assertEqual(services.runtimes.runtimes(),[])
                caps=app.test_client().get('/api/capabilities').get_json()
                self.assertFalse(caps['reveal_file'])
                services.start();services.start()
                threads=pipeline._threads+pipeline.notes._threads+pipeline.reviews._threads
                self.assertEqual(len(threads),4)
                self.assertTrue(all(thread.is_alive() for thread in threads))
            finally: services.close()
            self.assertFalse(any(thread.is_alive() for thread in threads))

    def test_scopes_do_not_leak_and_background_worker_inherits_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            first=Services.create(Path(tmp)/'one');second=Services.create(Path(tmp)/'two')
            observed=[]
            try:
                with first.scope():
                    self.assertIs(configured_settings(),first.ai)
                    with second.scope():
                        thread=start_thread(lambda:observed.append(configured_settings()),'test-service-context')
                        thread.join(2)
                    self.assertIs(current_services(),first)
                self.assertIsNone(current_services())
                self.assertEqual(observed,[second.ai])
            finally: first.close();second.close()
