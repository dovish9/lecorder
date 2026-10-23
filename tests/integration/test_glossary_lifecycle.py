import json
from unittest.mock import patch
from tests.note_support import NoteCase
from web.backend.speech.transcription import TranscriptionCancelled

class GlossaryLifecycleTests(NoteCase):
    def prepare(self):
        note=self.ready();rev=note['revision_id']
        page=self.notes.pages(rev)[0];page['analysis']={'markdown':'Coulomb force','summary':'force','symbols':[]}
        self.notes._page(rev,page)
        self.notes._update(rev,glossary_data=json.dumps({'candidates':note['keywords']}))
        return note

    def test_regeneration_keeps_original_and_publishes_new_snapshot(self):
        note=self.prepare();before=self.notes.snapshot(note['revision_id'])
        pending=self.notes.regenerate_glossary(note['id'])
        self.assertNotEqual(pending['revision_id'],note['revision_id'])
        self.assertEqual(self.notes.snapshot(note['revision_id']),before)
        self.assertEqual(self.notes.queue_items()[0]['stage'],'glossary')
        result=[dict(note['keywords'][0],meaning='힘',spoken_forms=['쿨롱'])]
        with patch('web.backend.notes.notes.build_glossary',return_value=(result,{'status':'passed'})), patch('web.backend.notes.notes.build_overview') as overview,patch('web.backend.notes.notes.analyze_page') as page:
            self.notes.study(pending['revision_id'])
        overview.assert_not_called();page.assert_not_called()
        current=self.notes.get(note['id'])
        self.assertEqual(current['study_status'],'completed')
        self.assertEqual(current['keywords'][0]['spoken_forms'],['쿨롱'])
        self.assertEqual(self.notes.snapshot(pending['revision_id'])['keywords'][0]['spoken_forms'],['쿨롱'])
        self.assertEqual(self.notes.snapshot(note['revision_id']),before)
        selected=self.notes.select_keywords(note['id'],[result[0]['id']])
        self.assertEqual(selected['glossary']['validation']['status'],'passed')
        self.assertFalse(selected['keywords'][0]['included'])

    def test_failure_and_cancel_preserve_confirmed_keywords(self):
        for error in [ValueError('bad response'),TranscriptionCancelled()]:
            note=self.prepare();pending=self.notes.regenerate_glossary(note['id'])
            with patch('web.backend.notes.notes.build_glossary',side_effect=error):self.notes.study(pending['revision_id'])
            current=self.notes.get(note['id'])
            self.assertEqual(current['keywords'],note['keywords'])
            self.assertIn(current['study_status'],{'failed','cancelled'})
            self.assertEqual(self.notes.queue_items(),[])

    def test_cancelled_queued_regeneration_makes_no_request(self):
        note=self.prepare();pending=self.notes.regenerate_glossary(note['id']);self.notes.cancel(note['id'])
        with patch('web.backend.notes.notes.build_glossary') as build:self.notes.study(pending['revision_id'])
        build.assert_not_called()

    def test_regeneration_route_and_duplicate_rejection(self):
        from flask import Flask
        from web.backend.notes.note_routes import note_routes
        note=self.prepare();app=Flask(__name__);app.register_blueprint(note_routes(self.notes,None));client=app.test_client()
        url=f"/api/notes/{note['id']}/glossary"
        self.assertEqual(client.post(url).status_code,202)
        self.assertEqual(client.post(url).status_code,400)

    def test_restart_resumes_glossary_without_reanalyzing_pages(self):
        from web.backend.notes.notes import NoteLibrary
        note=self.prepare();pending=self.notes.regenerate_glossary(note['id'])
        restarted=NoteLibrary(self.store,self.notes.folder,start_worker=False)
        self.addCleanup(restarted.close)
        with patch('web.backend.notes.notes.build_glossary',return_value=(note['keywords'],{'status':'passed'})),patch('web.backend.notes.notes.analyze_page') as page,patch('web.backend.notes.notes.build_overview') as overview:
            restarted.study(pending['revision_id'])
        page.assert_not_called();overview.assert_not_called()
        self.assertEqual(restarted.get(note['id'])['study_status'],'completed')

    def test_late_result_after_cancellation_cannot_publish(self):
        note=self.prepare();pending=self.notes.regenerate_glossary(note['id'])
        def late(*args):
            self.notes.cancel(note['id'])
            return [dict(note['keywords'][0],term='late')],{'status':'passed'}
        with patch('web.backend.notes.notes.build_glossary',side_effect=late):self.notes.study(pending['revision_id'])
        current=self.notes.get(note['id'])
        self.assertEqual(current['study_status'],'cancelled')
        self.assertEqual(current['keywords'],note['keywords'])

    def test_unpublished_failed_glossary_cannot_supply_raw_hints(self):
        note=self.prepare()
        self.notes._update(note['revision_id'],study_status='failed',glossary_data=json.dumps({'version':'glossary-v1','published':False}))
        with self.assertRaisesRegex(ValueError,'용어집 생성'):
            self.notes.bind(note['id'],note['course_id'])

    def test_glossary_regeneration_does_not_disable_later_detailed_explanation(self):
        note=self.prepare();pending=self.notes.regenerate_glossary(note['id'])
        with patch('web.backend.notes.notes.build_glossary',return_value=(note['keywords'],{'status':'passed'})):
            self.notes.study(pending['revision_id'])
        current=self.notes.get(note['id'])
        with patch('web.backend.notes.notes.analyze_page',return_value={'markdown':'Detailed explanation'}) as analyze:
            self.notes.study(current['revision_id'],1)
        analyze.assert_called_once()
        self.assertEqual(self.notes.pages(current['revision_id'])[0]['detail_status'],'completed')
