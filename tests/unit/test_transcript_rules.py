from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

from web.backend.speech.engine import Transcriber
from web.backend.markdown import render_note
from web.backend.library.records import Recording
from web.backend.speech.review import (
    combined_review_prompt,
    reviewer_prompt,
    validated_suggestions,
)
from web.backend.speech.transcription import Options, TranscriptSegment, WhisperResult


from tests.support import RecorderCase

class TranscriptRulesTests(RecorderCase):
    def setUp(self):
        super().setUp()
        runtime = patch('web.backend.speech.speech_processing.runtime_for')
        runtime.start()
        self.addCleanup(runtime.stop)

    def test_reviewer_requests_suggestions_and_applies_only_verifiable_edits(self) -> None:
        options = Options(language="ko", course_name="통계학", recognition_hint="모집단, 표본", llm_model="test")
        self.assertIn("과목명: 통계학", reviewer_prompt(options))
        self.assertIn("제안 누락이 잘못된 제안보다 낫다", reviewer_prompt(options))
        self.assertIn("한국어와 영어 표기를 서로 바꾸지 않는다", reviewer_prompt(options))
        self.assertIn("의미만 비슷한 교체", reviewer_prompt(options))
        absolute_phonetic_rule = "뜻·문맥·철자·전문용어 여부·confidence와 관계없이 무조건 제안하지 않는다"
        self.assertIn(absolute_phonetic_rule, reviewer_prompt(options))
        self.assertIn(absolute_phonetic_rule, combined_review_prompt(options))
        self.assertIn("technical_term도 발음 조건을 예외로 면제하지 않는다", reviewer_prompt(options))
        self.assertIn("technical_term도 발음 조건을 예외로 면제하지 않는다", combined_review_prompt(options))
        self.assertIn("개수나 길이를 맞추려고 나누지 않는다", combined_review_prompt(options))
        self.assertIn("JSON에는 edits와 break_after만", combined_review_prompt(options))
        review_response = Mock()
        review_response.raise_for_status.return_value = None
        review_response.json.return_value = {"message": {"content": json.dumps({
            "edits": [
                {"sentence_id": "s00001", "original": "표정을", "replacement": "표본을",
                 "error_type": "technical_term", "reason": "힌트의 표본과 발음이 유사함", "confidence": .96},
                {"sentence_id": "s00002", "original": "없는 말", "replacement": "조작",
                 "error_type": "phonetic_asr", "reason": "발음이 유사함", "confidence": .99},
            ],
            "break_after": ["s00001"],
        }, ensure_ascii=False)}}
        with patch(
            "web.backend.speech.review_engine.generate", return_value={"result": json.loads(review_response.json.return_value["message"]["content"]), "input_tokens": 0, "output_tokens": 0, "seconds": .1, "waiting_seconds": 0}
        ) as post:
            value, total, fallback, suggestions = Transcriber._review(
                "이 표정을 이용합니다. 다음 설명입니다.", options, None
            )
        self.assertEqual((total, fallback), (1, 0))
        self.assertEqual(len(suggestions), 1)
        self.assertEqual(value, "이 표정을 이용합니다.\n\n다음 설명입니다.")
        self.assertEqual(post.call_count, 1)
        review_payload = {"format": post.call_args.args[1], "messages": post.call_args.args[0]}
        self.assertIn("edits", review_payload["format"]["properties"])
        self.assertIn("break_after", review_payload["format"]["properties"])
        self.assertIn("앞 문맥(후보 단어·경계 확인 전용, 수정 금지)", review_payload["messages"][1]["content"])


    def test_suggestion_validator_rejects_copyediting_and_clause_rewrites(self) -> None:
        sentence_map = {
            "s00001": "그래서 이런 식으로 되어야지.",
            "s00002": "그럼 1000명 늘잖아요.",
            "s00003": "공주형 자료가 있어요.",
            "s00004": "우리에게 가장 중요한 거는 숫자가 아니다.",
        }
        candidates = [
            {"sentence_id": "s00001", "original": "되어야지", "replacement": "되어야 해",
             "error_type": "phonetic_asr", "reason": "말투 조정", "confidence": .99},
            {"sentence_id": "s00002", "original": "1000명", "replacement": "1000명이",
             "error_type": "phonetic_asr", "reason": "조사 보완", "confidence": .99},
            {"sentence_id": "s00004", "original": "중요한 거는 숫자가 아니다",
             "replacement": "중요한 건 분석 방법이다", "error_type": "technical_term",
             "reason": "강의 핵심을 명확히 함", "confidence": .99},
            {"sentence_id": "s00003", "original": "공주형 자료", "replacement": "범주형 자료",
             "error_type": "technical_term", "reason": "통계 용어와 발음이 유사함", "confidence": .97},
        ]
        suggestions = validated_suggestions(sentence_map, candidates)
        self.assertEqual([(item.original, item.replacement) for item in suggestions], [
            ("공주형 자료", "범주형 자료"),
        ])
        unhinted_term = validated_suggestions({"s00001": "공주형 자료가 있어요."}, [{
            "sentence_id": "s00001", "original": "공주형 자료", "replacement": "범주형 자료",
            "error_type": "technical_term", "reason": "통계 용어와 발음이 유사함",
            "confidence": .97,
        }])
        self.assertEqual([(item.original, item.replacement) for item in unhinted_term], [
            ("공주형 자료", "범주형 자료"),
        ])
        unsupported = validated_suggestions({"s00001": "아무것도 운영하고 있어."}, [{
            "sentence_id": "s00001", "original": "운영", "replacement": "관리",
            "error_type": "technical_term", "reason": "통계 용어 후보", "confidence": .98,
        }])
        self.assertEqual(unsupported, [])
        phonetic = validated_suggestions({"s00001": "설리해서 뽑거든."}, [{
            "sentence_id": "s00001", "original": "설리해서", "replacement": "설계해서",
            "error_type": "phonetic_asr", "reason": "두 표현의 발음이 유사함", "confidence": .96,
        }])
        self.assertEqual([(item.original, item.replacement) for item in phonetic], [
            ("설리해서", "설계해서"),
        ])
        threshold = validated_suggestions({"s00001": "공주형 자료가 있어요."}, [
            {
                "sentence_id": "s00001", "original": "공주형 자료", "replacement": "범주형 자료",
                "error_type": "technical_term", "reason": "통계 용어와 발음이 유사함",
                "confidence": .80,
            },
        ])
        self.assertEqual(len(threshold), 1)
        below_threshold = validated_suggestions({"s00001": "공주형 자료가 있어요."}, [
            {
                "sentence_id": "s00001", "original": "공주형 자료", "replacement": "범주형 자료",
                "error_type": "technical_term", "reason": "통계 용어와 발음이 유사함",
                "confidence": .799,
            },
        ])
        self.assertEqual(below_threshold, [])


    def test_suggestion_validator_rejects_observed_semantic_and_spelling_edits(self) -> None:
        observed = [
            ("파퓰레이션", "모집단", "technical_term", "통계학 용어 모집단이 오인식됨"),
            ("들어갈 확률이", "포함될 확률이", "technical_term", "포함될 확률이 전문용어로 사용됨"),
            ("몰수가", "모집단", "technical_term", "강의 힌트에 모집단이 사용됨"),
            ("유머리컬", "범주형", "technical_term", "범주형 자료가 정확한 표현"),
            ("모집단을 예측하는 그런", "모집단을 추정하는 그런", "technical_term", "통계학에서 추정을 의미"),
            ("100명만 있으면 돼", "100명만 있으면 되", "phonetic_asr", "발음이 가까움"),
            ("KT에", "추적", "phonetic_asr", "발음상 추적과 유사함"),
        ]
        sentence_map = {
            f"s{index:05d}": f"앞 문맥 {original} 뒤 문맥"
            for index, (original, _, _, _) in enumerate(observed, 1)
        }
        candidates = [
            {
                "sentence_id": f"s{index:05d}", "original": original,
                "replacement": replacement, "error_type": error_type,
                "reason": reason, "confidence": .99,
            }
            for index, (original, replacement, error_type, reason) in enumerate(observed, 1)
        ]
        suggestions = validated_suggestions(sentence_map, candidates)
        self.assertEqual(suggestions, [])


    def test_reviewer_passes_whisper_confidence_only_to_word_review(self) -> None:
        options = Options(language="ko", course_name="통계학", llm_model="test", format_text=False)
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"message": {"content": json.dumps({"edits": []})}}
        with patch(
            "web.backend.speech.review_engine.generate", return_value={"result": json.loads(response.json.return_value["message"]["content"]), "input_tokens": 0, "output_tokens": 0, "seconds": .1, "waiting_seconds": 0}
        ) as post:
            Transcriber._review_items(
                ["공주형 자료가 있어요."], options, None, {"s00001": -.8234}
            )
        content = post.call_args.args[0][1]["content"]
        self.assertIn("[s00001 avg_logprob=-0.823]", content)
        self.assertEqual(post.call_count, 1)


    def test_reviewer_does_not_force_length_based_paragraphs(self) -> None:
        options = Options(language="ko", course_name="강의", llm_model="test")
        source = " ".join(f"같은 주제의 설명 {index}." for index in range(80))
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "message": {"content": json.dumps({"edits": [], "break_after": []})}
        }
        with patch(
            "web.backend.speech.review_engine.generate", return_value={"result": json.loads(response.json.return_value["message"]["content"]), "input_tokens": 0, "output_tokens": 0, "seconds": .1, "waiting_seconds": 0}
        ):
            value, _, fallback, _ = Transcriber._review(source, options, None)
        self.assertEqual(fallback, 0)
        self.assertNotIn("\n\n", value)


    def test_reviewer_splits_and_retries_truncated_json(self) -> None:
        options = Options(language="ko", course_name="통계학", recognition_hint="모집단, 표본", llm_model="test")
        truncated = Mock()
        truncated.raise_for_status.return_value = None
        truncated.json.return_value = {
            "done_reason": "length", "message": {"content": '{"edits":['}
        }
        corrected = Mock()
        corrected.raise_for_status.return_value = None
        corrected.json.return_value = {"message": {"content": json.dumps({
            "edits": [{
                "sentence_id": "s00001", "original": "표정을", "replacement": "표본을",
                "error_type": "technical_term", "reason": "힌트의 표본과 발음이 유사함",
                "confidence": .96,
            }],
            "break_after": ["s00001"],
        }, ensure_ascii=False)}}
        untouched = Mock()
        untouched.raise_for_status.return_value = None
        untouched.json.return_value = {"message": {"content": json.dumps({
            "edits": [],
            "break_after": [],
        })}}
        with patch(
            "web.backend.speech.review_engine.generate", side_effect=[ValueError("truncated"), *[{"result": json.loads(r.json.return_value["message"]["content"]), "input_tokens": 0, "output_tokens": 0, "seconds": .1, "waiting_seconds": 0} for r in (corrected, untouched)]]
        ) as post:
            value, total, fallback, suggestions = Transcriber._review(
                "이 표정을 이용합니다. 다음 설명입니다.", options, None
            )
        self.assertEqual(post.call_count, 3)
        self.assertEqual((total, fallback), (1, 0))
        self.assertEqual(len(suggestions), 1)
        self.assertEqual(value, "이 표정을 이용합니다.\n\n다음 설명입니다.")


    def test_whisper_verbose_json_preserves_segment_timestamps(self) -> None:
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "text": "첫 문장. 둘째 문장.", "duration": 9.25,
            "segments": [
                {"start": 0.0, "end": 4.0, "text": "첫 문장.", "avg_logprob": -.42,
                 "no_speech_prob": .01},
                {"start": 4.0, "end": 9.25, "text": "둘째 문장.", "avg_logprob": -.71,
                 "no_speech_prob": .02},
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            wav = Path(temporary) / "audio.wav"
            wav.write_bytes(b"wav")
            with patch("web.backend.speech.speech_processing.request_json", return_value=response.json.return_value) as post:
                result = Transcriber._whisper(wav, Options(language="ko"), None)
        self.assertEqual(result.duration_seconds, 9.25)
        self.assertEqual(result.segments[1].start, 4.0)
        self.assertEqual(result.segments[1].avg_logprob, -.71)
        self.assertEqual(post.call_args.kwargs["multipart"]["response_format"], "verbose_json")
        self.assertEqual(post.call_args.kwargs["multipart"]["entropy_thold"], "2.8")
        self.assertEqual(post.call_args.kwargs["multipart"]["no_context"], "true")
        self.assertEqual(post.call_args.kwargs["multipart"]["vad"], "true")
        self.assertEqual(post.call_args.kwargs["multipart"]["vad_min_silence_duration_ms"], "300")
        with tempfile.TemporaryDirectory() as temporary:
            wav = Path(temporary) / "audio.wav"
            wav.write_bytes(b"wav")
            with patch("web.backend.speech.speech_processing.request_json", return_value=response.json.return_value) as post:
                Transcriber._whisper(wav, Options(language="en"), None)
        self.assertEqual(post.call_args.kwargs["multipart"]["no_context"], "true")


    def test_vad_result_with_implausible_early_cutoff_falls_back_safely(self) -> None:
        dropped = Mock()
        dropped.raise_for_status.return_value = None
        dropped.json.return_value = {
            "text": "앞부분은 충분히 길게 전사됐지만 후반부가 누락되었습니다. " * 20,
            "duration": 180,
            "segments": [{"start": 0, "end": 60, "text": "앞부분 전사", "avg_logprob": -.4}],
        }
        complete = Mock()
        complete.raise_for_status.return_value = None
        complete.json.return_value = {
            "text": "전체 강의 내용 " * 50, "duration": 180,
            "segments": [{"start": 0, "end": 178, "text": "전체 강의 내용", "avg_logprob": -.5}],
        }
        progress = []
        with tempfile.TemporaryDirectory() as temporary:
            wav = Path(temporary) / "audio.wav"
            wav.write_bytes(b"wav")
            with patch("web.backend.speech.speech_processing.request_json", side_effect=[dropped.json.return_value, complete.json.return_value]) as post:
                result = Transcriber._whisper(
                    wav, Options(language="ko"), None,
                    lambda stage, index, total: progress.append((stage, index, total)),
                )
        self.assertTrue(result.vad_fallback)
        self.assertEqual(post.call_count, 2)
        self.assertEqual(post.call_args_list[1].kwargs["multipart"]["vad"], "false")
        self.assertEqual(progress, [("vad_fallback", 1, 1)])


    def test_primary_repetition_collapse_resets_context_and_vad(self) -> None:
        repeated_segments = [
            {
                "start": index * 2, "end": index * 2 + 2,
                "text": (
                    "And then I'm going to ask the PAs to come in and do a little bit of a test."
                    if index % 2 else "Yeah."
                ),
                "avg_logprob": -.01,
            }
            for index in range(60)
        ]
        collapsed = Mock()
        collapsed.raise_for_status.return_value = None
        collapsed.json.return_value = {
            "text": " ".join(item["text"] for item in repeated_segments),
            "duration": 120,
            "segments": repeated_segments,
        }
        recovered = Mock()
        recovered.raise_for_status.return_value = None
        recovered.json.return_value = {
            "text": "Now we return to the diagram and calculate the electric field.",
            "duration": 120,
            "segments": [{
                "start": 0, "end": 118,
                "text": "Now we return to the diagram and calculate the electric field.",
                "avg_logprob": -.3,
            }],
        }
        progress = []
        with tempfile.TemporaryDirectory() as temporary:
            wav = Path(temporary) / "audio.wav"
            wav.write_bytes(b"wav")
            with patch(
                "web.backend.speech.speech_processing.request_json", side_effect=[collapsed.json.return_value, recovered.json.return_value]
            ) as post:
                result = Transcriber._whisper(
                    wav, Options(language="en", recognition_hint="electric field"), None,
                    lambda stage, index, total: progress.append((stage, index, total)),
                )

        self.assertTrue(result.repetition_detected)
        self.assertGreater(result.repetition_ratio, .9)
        self.assertFalse(result.vad_fallback)
        self.assertEqual(result.text, recovered.json.return_value["text"])
        self.assertEqual(progress, [("repetition_fallback", 1, 1)])
        fallback_data = post.call_args_list[1].kwargs["multipart"]
        self.assertEqual(fallback_data["vad"], "false")
        self.assertEqual(fallback_data["temperature_inc"], "0.2")
        self.assertEqual(fallback_data["beam_size"], "5")
        self.assertEqual(fallback_data["no_context"], "true")
        self.assertEqual(fallback_data["suppress_nst"], "true")
        self.assertEqual(fallback_data["prompt"], "")


    def test_repetition_collapse_never_saves_a_second_collapsed_result(self) -> None:
        segments = [
            {"start": index, "end": index + 1, "text": "The same long hallucinated sentence.",
             "avg_logprob": 0.0}
            for index in range(40)
        ]
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "text": " ".join(item["text"] for item in segments),
            "duration": 40,
            "segments": segments,
        }
        with tempfile.TemporaryDirectory() as temporary:
            wav = Path(temporary) / "audio.wav"
            wav.write_bytes(b"wav")
            with patch("web.backend.speech.speech_processing.request_json", return_value=response.json.return_value):
                with self.assertRaisesRegex(RuntimeError, "동일 발화를 반복 생성"):
                    Transcriber._whisper(wav, Options(language="en"), None)


    def test_repetition_profile_allows_ordinary_lecture_restatements(self) -> None:
        segments = tuple(
            TranscriptSegment(
                index, index * 2, index * 2 + 2,
                "Yes." if index in {4, 11, 19, 28} else f"Distinct lecture point number {index}.",
                -.3,
            )
            for index in range(40)
        )
        collapsed, ratio = Transcriber._repetition_profile(segments)
        self.assertFalse(collapsed)
        self.assertLess(ratio, .2)


    def test_low_confidence_groups_are_bounded_and_adjacent(self) -> None:
        items = (
            TranscriptSegment(1, 0, 3, "good", -.2),
            TranscriptSegment(2, 3, 7, "weak one", -.8),
            TranscriptSegment(3, 7, 11, "weak two", -.9),
            TranscriptSegment(4, 11, 15, "good", -.3),
            TranscriptSegment(5, 15, 50, "long weak", -.8),
        )
        self.assertEqual(Transcriber._low_confidence_groups(items, -.65), [(1, 2), (4, 4)])


    def test_low_confidence_retry_replaces_only_a_better_candidate(self) -> None:
        original = WhisperResult(
            "wrong term", 8.0,
            (TranscriptSegment(1, 2.0, 6.0, "wrong term", -.9, .01),),
        )
        better = WhisperResult(
            "correct term", 6.5,
            (TranscriptSegment(1, 1.25, 5.25, "correct term", -.3, .01),),
        )
        options = Options(language="en", recognition_hint="correct term")
        progress = []
        with patch("web.backend.speech.speech_quality.media_request"), patch.object(
            Transcriber, "_whisper", return_value=better
        ):
            result = Transcriber._retry_low_confidence(
                Path("/tmp/audio.wav"), options, original, None,
                lambda stage, index, total: progress.append((stage, index, total)),
            )
        self.assertEqual(result.text, "correct term")
        self.assertTrue(result.segments[0].retried)
        self.assertEqual(result.low_confidence_segments, 1)
        self.assertEqual(result.retry_attempted_groups, 1)
        self.assertEqual(result.retry_accepted_groups, 1)
        self.assertEqual(progress, [("retry", 1, 1)])


    def test_low_confidence_retry_limit_can_disable_extra_work(self) -> None:
        original = WhisperResult(
            "weak", 4.0, (TranscriptSegment(1, 0.0, 4.0, "weak", -.9, .01),)
        )
        with patch.dict("os.environ", {
            "LECORDER_SPEECH_RETRY_MAX_GROUPS": "0", "LECORDER_SPEECH_LOW_CONFIDENCE": "-0.65",
        }), patch.object(
            Transcriber, "_whisper"
        ) as whisper:
            result = Transcriber._retry_low_confidence(
                Path("/tmp/audio.wav"), Options(language="en"), original, None
            )
        whisper.assert_not_called()
        self.assertEqual(result.low_confidence_segments, 1)
        self.assertEqual(result.retry_attempted_groups, 0)


    def test_hint_hits_count_distinct_course_terms(self) -> None:
        self.assertEqual(
            Transcriber._hint_hits("표본에서 모집단을 추론한다", "통계학, 모집단, 표본, 표본"),
            2,
        )


    def test_markdown_keeps_korean_audio_links_readable(self) -> None:
        recording = Recording(
            id="unicode-link",
            course_id=1,
            course_name="통계학",
            title="통계학-9월9일-수",
            source_kind="upload",
            language="ko",
            format_transcript=True,
            llm_enabled=False,
            llm_model="",
            output_dir="/tmp",
            extension=".webm",
            status="completed",
            created_at="2026-09-08T20:18:05+00:00",
            audio_path="/tmp/통계학-9월9일-수.webm",
            duration_seconds=8.54,
        )
        note = render_note(
            recording,
            [{"start": 2.0, "end": 3.0, "text": "표본입니다."}],
            set(),
        )
        self.assertIn("[녹음 듣기](통계학-9월9일-수.webm)", note)
        self.assertIn("[00:02](통계학-9월9일-수.webm#t=2)", note)
        self.assertNotIn("%ED", note)
        self.assertNotIn("# 통계학-9월9일-수", note)

        spaced = Recording(**{**recording.__dict__, "audio_path": "/tmp/통계학 강의 (2).webm"})
        spaced_note = render_note(spaced, [], set())
        self.assertIn("(통계학%20강의%20%282%29.webm)", spaced_note)


    def test_markdown_adds_one_timestamp_per_paragraph(self) -> None:
        recording = Recording(
            id="paragraph-links", course_id=1, course_name="통계학", title="강의",
            source_kind="upload", language="ko",
            format_transcript=True, llm_enabled=False, llm_model="", output_dir="/tmp",
            extension=".wav", status="completed", audio_path="/tmp/Lecture.wav",
        )
        note = render_note(recording, [
            {"start": 2.0, "text": "첫 문장입니다."},
            {"start": 6.0, "text": "둘째 문장입니다."},
            {"start": 10.0, "text": "다음 문단입니다."},
        ], {2})
        self.assertIn("[00:02](Lecture.wav#t=2) 첫 문장입니다. 둘째 문장입니다.", note)
        self.assertIn("[00:10](Lecture.wav#t=10) 다음 문단입니다.", note)
        self.assertEqual(note.count("#t="), 2)
