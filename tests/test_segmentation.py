import unittest

from voice_tools import AudioError
from voice_tools.models import TimedWord
from voice_tools.segmentation import align_units, plan_sentences, validate_boundaries


class SegmentationTests(unittest.TestCase):
    def test_japanese_surface_and_punctuation_preserved(self):
        units = align_units("今日は、いい天気です。そうですね！", [
            TimedWord("今日", 0, .4), TimedWord("は", .4, .6),
            TimedWord("いい", .8, 1), TimedWord("天気", 1, 1.4),
            TimedWord("です", 1.4, 1.7), TimedWord("そう", 2, 2.4),
            TimedWord("です", 2.4, 2.6), TimedWord("ね", 2.6, 2.8),
        ], 3)
        self.assertEqual("".join(u.text for u in units), "今日は、いい天気です。そうですね！")
        sentences = plan_sentences(units, duration=3)
        self.assertEqual([s.text for s in sentences], ["今日は、いい天気です。", "そうですね！"])
        self.assertLessEqual(sentences[0].cut_end, sentences[1].cut_start)
        self.assertEqual(sentences[0].speech_start, 0)

    def test_alignment_missing_text_and_bad_timestamps_fail_closed(self):
        for words in [
            [TimedWord("今日", 0, 1)],
            [TimedWord("今日は", 1, 0)],
            [TimedWord("今日は", 0, float("nan"))],
            [TimedWord("今日は", 0, 5)],
            [TimedWord("今日", 0, 1), TimedWord("は", .5, .8)],
        ]:
            with self.subTest(words=words), self.assertRaises(AudioError):
                align_units("今日は。", words, 2)

    def test_normalized_width_and_quotes_preserve_original(self):
        units = align_units("「ＡＢＣ、ｶﾞ。」", [
            TimedWord("ABC", .1, .5), TimedWord("ガ", .6, .9),
        ], 1)
        self.assertEqual("".join(u.text for u in units), "「ＡＢＣ、ｶﾞ。」")

    def test_long_pause_does_not_alone_end_a_sentence(self):
        units = align_units("今日は、いい天気です。", [
            TimedWord("今日は", .1, .5), TimedWord("いい", 2, 2.3),
            TimedWord("天気です", 2.3, 3),
        ], 4)
        self.assertEqual(len(plan_sentences(units, duration=4)), 1)

    def test_missing_final_punctuation_is_reviewable(self):
        units = align_units("まだ話している", [TimedWord("まだ話している", 0, 1)], 2)
        self.assertIn("incomplete_or_unpunctuated", plan_sentences(units, duration=2)[0].flags)

    def test_quantized_zero_duration_and_single_bin_tail_are_reviewable(self):
        units = align_units("今日は。", [TimedWord("今日", .1, .5),
                                       TimedWord("は", .8, .8)], 1)
        self.assertIn("zero_duration_unit", plan_sentences(units, duration=1)[0].flags)
        clipped = align_units("はい。", [TimedWord("はい", .5, 1.08)], 1)
        self.assertEqual(clipped[0].end, 1)
        self.assertIn("alignment_boundary_clipped", clipped[0].flags)
        with self.assertRaises(AudioError):
            align_units("はい。", [TimedWord("はい", .5, 1.2)], 1)

    def test_llm_indices_cannot_skip_repeat_reorder_or_invent(self):
        for boundaries in [[1], [2, 1, 3], [1, 1, 3], [4], [True, 3], [], ["3"]]:
            with self.subTest(boundaries=boundaries), self.assertRaises(AudioError):
                validate_boundaries(boundaries, 3)
        self.assertEqual(validate_boundaries([1, 3], 3), [1, 3])

    def test_zero_duration_sentence_merges_forward_without_losing_text_or_times(self):
        units = align_units("はい。あら。次です。", [
            TimedWord("はい", .1, .5), TimedWord("あら", .6, .6),
            TimedWord("次です", .6, 1.2),
        ], 2)
        sentences = plan_sentences(units, duration=2)
        self.assertEqual([s.text for s in sentences], ["はい。", "あら。次です。"])
        self.assertEqual((sentences[1].first_unit, sentences[1].last_unit), (2, 3))
        self.assertEqual((sentences[1].speech_start, sentences[1].speech_end), (.6, 1.2))
        self.assertIn("zero_duration_sentence_merged", sentences[1].flags)
        self.assertEqual((units[1].start, units[1].end), (.6, .6))

    def test_zero_duration_prefix_and_suffix_stay_in_reviewable_clip(self):
        units = align_units("あ。え。はい。う。", [
            TimedWord("あ", .1, .1), TimedWord("え", .1, .1),
            TimedWord("はい", .2, .8), TimedWord("う", .9, .9),
        ], 1)
        sentences = plan_sentences(units, duration=1, boundaries=[1, 2, 3, 4])
        self.assertEqual(len(sentences), 1)
        self.assertEqual(sentences[0].text, "あ。え。はい。う。")
        self.assertIn("zero_duration_sentence_merged", sentences[0].flags)
        self.assertEqual((sentences[0].first_unit, sentences[0].last_unit), (1, 4))

    def test_entirely_zero_duration_alignment_still_fails(self):
        units = align_units("あ。え。", [TimedWord("あ", .1, .1),
                                      TimedWord("え", .2, .2)], 1)
        with self.assertRaisesRegex(AudioError, "正.*時|正.*时"):
            plan_sentences(units, duration=1)
        with self.assertRaisesRegex(AudioError, "正.*时"):
            plan_sentences(units, duration=1, boundaries=[2])

    def test_outside_tail_is_explicitly_retained_for_review_not_clamped(self):
        words = [TimedWord("はい", .1, .5), TimedWord("違う", 1.2, 1.4)]
        with self.assertRaises(AudioError):
            align_units("はい。違う。", words, 1)
        units = align_units("はい。違う。", words, 1, allow_outside_tail=True)
        self.assertEqual("".join(u.text for u in units), "はい。違う。")
        self.assertEqual(units[-1].start, 1.2)
        self.assertIn("outside_audio", units[-1].flags)

    def test_context_tail_overrun_is_retained_only_outside_owned_core(self):
        words = [TimedWord("です", 23.92, 24), TimedWord("けれど", 24, 24.24),
                 TimedWord("も", 24.24, 25.04)]
        units = align_units("ですけれども。", words, 24.6,
                            allow_outside_tail=True, core_end=24)
        self.assertEqual("".join(u.text for u in units), "ですけれども。")
        self.assertEqual((units[-1].start, units[-1].end), (24.24, 25.04))
        self.assertIn("context_tail_overrun", units[-1].flags)
        for options in [{}, {"allow_outside_tail": True},
                        {"allow_outside_tail": True, "core_end": 24.4}]:
            with self.subTest(options=options), self.assertRaisesRegex(AudioError, "越界"):
                align_units("ですけれども。", words, 24.6, **options)

    def test_english_decimal_is_not_sentence_end(self):
        units = align_units("It costs 3.14 dollars. Yes!", [
            TimedWord("It", 0, .2), TimedWord("costs", .2, .4),
            TimedWord("3.14", .4, .8), TimedWord("dollars", .8, 1.1),
            TimedWord("Yes", 1.3, 1.5),
        ], 2)
        self.assertEqual([s.text for s in plan_sentences(units, duration=2)],
                         ["It costs 3.14 dollars.", "Yes!"])

    def test_decimal_remains_whole_when_aligner_splits_its_digits(self):
        units = align_units("It costs 3.14 dollars. Yes!", [
            TimedWord("It", 0, .2), TimedWord("costs", .2, .4),
            TimedWord("3", .4, .5), TimedWord("14", .5, .8),
            TimedWord("dollars", .8, 1.1), TimedWord("Yes", 1.3, 1.5),
        ], 2)
        self.assertEqual([s.text for s in plan_sentences(units, duration=2)],
                         ["It costs 3.14 dollars.", "Yes!"])


if __name__ == "__main__":
    unittest.main()
