from oban._backoff import exponential, jitter, jittery_clamped


class TestExponential:
    def test_basic_exponential_backoff(self):
        assert exponential(0) == 1
        assert exponential(1) == 2
        assert exponential(2) == 4
        assert exponential(3) == 8

    def test_with_multiplier(self):
        assert exponential(2, mult=5) == 20

    def test_with_min_pad(self):
        assert exponential(0, min_pad=10) == 11
        assert exponential(1, min_pad=10) == 12

    def test_with_max_pow(self):
        assert exponential(10, max_pow=5) == 32
        assert exponential(100, max_pow=5) == 32


class TestJitter:
    def test_inc_mode_always_increases(self):
        for _ in [10, 50, 100]:
            assert jitter(100, mode="inc", mult=0.1) >= 100

    def test_dec_mode_always_decreases(self):
        for _ in [10, 50, 100]:
            assert jitter(100, mode="dec", mult=0.1) <= 100

    def test_both_mode_stays_in_range(self):
        for _ in [10, 50, 100]:
            result = jitter(100, mode="both", mult=0.1)
            assert 90 <= result <= 110

    def test_custom_multiplier(self):
        for _ in [10, 50, 100]:
            result = jitter(100, mode="inc", mult=0.5)
            assert 100 <= result <= 150


class TestJitteryClamped:
    def test_small_max_attempts_doesnt_clamp(self):
        assert jittery_clamped(1, 10) >= 17

    def test_large_max_attempts_clamps(self):
        result_1 = jittery_clamped(50, 100)
        result_2 = jittery_clamped(100, 100)

        assert result_1 > 0
        assert result_2 > result_1

    def test_respects_custom_clamped_max(self):
        assert jittery_clamped(10, 10, clamped_max=5) > 0

    def test_always_positive(self):
        assert jittery_clamped(1, 1) > 0
        assert jittery_clamped(1, 20) > 0
        assert jittery_clamped(10, 20) > 0
        assert jittery_clamped(50, 100) > 0
