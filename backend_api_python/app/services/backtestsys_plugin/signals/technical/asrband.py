"""ASR-Band v6 Codex signal — Python port of asrband_v6_codex.pine.

Implements:
  Section 2: VoV computation (EWM of True Range)
  Section 3: Channel lines (orange/blue/yellow/cyan + bands + mid)
  Section 4: Trend state machine (close-based, v6 codex variant)
  Section 5: Signal engine (L1-L4, S1-S4 + TP/SL + cooldown + priority)
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.services.backtestsys_plugin.signals.base import Signal, SignalFrame, SignalType
from app.services.backtestsys_plugin.signals.registry import SignalRegistry


# ---------------------------------------------------------------------------
# Helper: manual EMA with SMA seed (matches Pine ta.ema)
# ---------------------------------------------------------------------------

def _ema_span(vals: np.ndarray, span: int, fallback_seed: int = 0) -> np.ndarray:
    """Manual EMA over a numpy array.

    - If len(vals) >= span: uses SMA(span) as seed at bar span-1.
    - Elif fallback_seed > 0 and len(vals) >= fallback_seed: SMA(fallback_seed) seed.
    - Else: returns all NaN.
    """
    n = len(vals)
    alpha = 2.0 / (span + 1)
    result = np.full(n, np.nan)

    if n >= span:
        seed_len = span
    elif fallback_seed > 0 and n >= fallback_seed:
        seed_len = fallback_seed
    else:
        return result

    result[seed_len - 1] = np.mean(vals[:seed_len])
    for i in range(seed_len, n):
        result[i] = alpha * vals[i] + (1.0 - alpha) * result[i - 1]
    return result


def _pullback_entry_gate(
    ts_prev: int,
    ts: int,
    *,
    is_long: bool,
    strict_pullback_trend_filter: bool,
    compress_l1_signals: bool,
    is_l1_level: bool,
) -> bool:
    """Return whether a pullback entry is allowed for the current bar.

    The gate is compositional:
    - baseline pullbacks require a confirmed previous trend
    - strict mode requires previous strong trend + current trend not broken
    - L1/S1 compression adds a stronger current-trend requirement
    """
    if is_long:
        confirmed_prev = ts_prev >= 1
        strong_prev = ts_prev == 2
        current_non_broken = ts >= 1
        current_strong = ts == 2
    else:
        confirmed_prev = ts_prev <= -1
        strong_prev = ts_prev == -2
        current_non_broken = ts <= -1
        current_strong = ts == -2

    allowed = confirmed_prev
    if strict_pullback_trend_filter:
        allowed = strong_prev and current_non_broken
    if compress_l1_signals and is_l1_level:
        allowed = allowed and current_strong
    return allowed


# ---------------------------------------------------------------------------
# AsrBandSignal
# ---------------------------------------------------------------------------

@SignalRegistry.register("asrband")
class AsrBandSignal(Signal):
    """ASR-Band v6 Codex indicator ported to Python.

    Produces trend state (values) and a rich metadata DataFrame with all
    channel lines and 8 entry signals + TP/SL.
    """

    def __init__(
        self,
        asr_length: int = 94,
        channel_width: float = 7.0,
        ewm_halflife: int = 178,
        band_mult: float = 0.25,
        cooldown_bars: int = 10,
        compress_l1_signals: bool = False,
        strict_pullback_trend_filter: bool = False,
    ):
        self._asr_length = asr_length
        self._channel_width = channel_width
        self._ewm_halflife = ewm_halflife
        self._band_mult = band_mult
        self._cooldown_bars = cooldown_bars
        self._compress_l1_signals = compress_l1_signals
        self._strict_pullback_trend_filter = strict_pullback_trend_filter

    # ------------------------------------------------------------------
    # Signal interface
    # ------------------------------------------------------------------

    @property
    def lookback(self) -> int:
        ewm_span = 2 * self._ewm_halflife - 1
        return max(self._asr_length, ewm_span)

    @property
    def params(self) -> dict:
        return {
            "asr_length": self._asr_length,
            "channel_width": self._channel_width,
            "ewm_halflife": self._ewm_halflife,
            "band_mult": self._band_mult,
            "cooldown_bars": self._cooldown_bars,
            "compress_l1_signals": self._compress_l1_signals,
            "strict_pullback_trend_filter": self._strict_pullback_trend_filter,
        }

    def compute(self, data: pd.DataFrame) -> SignalFrame:
        high = data["high"].values.astype(float)
        low = data["low"].values.astype(float)
        close = data["close"].values.astype(float)
        n = len(data)

        asr_len = self._asr_length
        cw = self._channel_width
        bm = self._band_mult
        cooldown = self._cooldown_bars
        ewm_span = 2 * self._ewm_halflife - 1

        # ==================================================================
        # Section 2: VoV computation
        # ==================================================================
        prev_close = np.empty(n)
        prev_close[0] = close[0]
        prev_close[1:] = close[:-1]

        tr = np.maximum(
            high - low,
            np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)),
        )
        tr_norm = tr / close

        ewm_tr = _ema_span(tr, ewm_span, fallback_seed=asr_len)
        ewm_tr_norm = _ema_span(tr_norm, ewm_span, fallback_seed=asr_len)

        vov_price = ewm_tr * cw
        band_vov_price = ewm_tr_norm * close * cw

        # ==================================================================
        # Section 3: Channel lines
        # ==================================================================
        # SMA of high / low using pandas rolling then extract values
        _high_s = pd.Series(high)
        _low_s = pd.Series(low)
        avg_high = _high_s.rolling(asr_len).mean().values
        avg_low = _low_s.rolling(asr_len).mean().values

        orange_line = avg_high + vov_price
        blue_line = avg_low - vov_price
        yellow_line = avg_high + vov_price / 2.0
        cyan_line = avg_low - vov_price / 2.0

        band_offset = bm * band_vov_price
        orange_band_upper = orange_line + band_offset
        orange_band_lower = orange_line - band_offset
        blue_band_upper = blue_line + band_offset
        blue_band_lower = blue_line - band_offset

        mid_line = (orange_line + blue_line) / 2.0

        # ==================================================================
        # Section 4: Trend state machine (v6 codex — close-based)
        # ==================================================================
        trend_state = np.zeros(n, dtype=np.int32)

        for i in range(1, n):
            prev = trend_state[i - 1]
            c = close[i]
            o = orange_line[i]
            b = blue_line[i]
            m = mid_line[i]

            if np.isnan(o) or np.isnan(b) or np.isnan(m):
                trend_state[i] = prev
                continue

            if c > o:
                trend_state[i] = 2
            elif c < b:
                trend_state[i] = -2
            elif prev == 2 and c < m:
                trend_state[i] = 1
            elif prev == -2 and c > m:
                trend_state[i] = -1
            else:
                trend_state[i] = prev

        # ==================================================================
        # Section 5: Signal engine
        # ==================================================================
        # Pre-compute shifted arrays (previous bar values)
        def _shift1(arr: np.ndarray) -> np.ndarray:
            out = np.empty_like(arr)
            out[0] = np.nan
            out[1:] = arr[:-1]
            return out

        low_prev = _shift1(low)
        high_prev = _shift1(high)
        close_prev = _shift1(close)
        cyan_prev = _shift1(cyan_line)
        bbu_prev = _shift1(blue_band_upper)
        blue_prev = _shift1(blue_line)
        yellow_prev = _shift1(yellow_line)
        obl_prev = _shift1(orange_band_lower)
        orange_prev = _shift1(orange_line)
        mid_prev = _shift1(mid_line)
        ts_prev = _shift1(trend_state.astype(float))

        # Boolean signal arrays
        long1 = np.zeros(n, dtype=bool)
        long2 = np.zeros(n, dtype=bool)
        long3 = np.zeros(n, dtype=bool)
        long4 = np.zeros(n, dtype=bool)
        short1 = np.zeros(n, dtype=bool)
        short2 = np.zeros(n, dtype=bool)
        short3 = np.zeros(n, dtype=bool)
        short4 = np.zeros(n, dtype=bool)

        long1_tp = np.zeros(n, dtype=bool)
        long2_tp = np.zeros(n, dtype=bool)
        long3_tp = np.zeros(n, dtype=bool)
        long4_close = np.zeros(n, dtype=bool)
        short1_tp = np.zeros(n, dtype=bool)
        short2_tp = np.zeros(n, dtype=bool)
        short3_tp = np.zeros(n, dtype=bool)
        short4_close = np.zeros(n, dtype=bool)

        all_long_sl = np.zeros(n, dtype=bool)
        all_short_sl = np.zeros(n, dtype=bool)

        # State tracking
        l1_open = False
        l2_open = False
        l3_open = False
        l4_open = False
        s1_open = False
        s2_open = False
        s3_open = False
        s4_open = False

        last_l1 = -cooldown - 1
        last_l2 = -cooldown - 1
        last_l3 = -cooldown - 1
        last_l4 = -cooldown - 1
        last_s1 = -cooldown - 1
        last_s2 = -cooldown - 1
        last_s3 = -cooldown - 1
        last_s4 = -cooldown - 1

        for i in range(1, n):
            # Skip bars where channels not ready
            if np.isnan(orange_line[i]) or np.isnan(blue_line[i]):
                continue

            ts = trend_state[i]
            ts_p = int(ts_prev[i]) if not np.isnan(ts_prev[i]) else 0

            bull_trend = ts >= 1
            bear_trend = ts <= -1
            bull_confirmed = ts_p >= 1
            bear_confirmed = ts_p <= -1

            # --- 5a: Touch conditions (first touch) ---
            touch_cyan = (low[i] <= cyan_line[i]) and (low_prev[i] > cyan_prev[i])
            touch_bbu = (low[i] <= blue_band_upper[i]) and (low_prev[i] > bbu_prev[i])
            touch_blue = (low[i] <= blue_line[i]) and (low_prev[i] > blue_prev[i])

            touch_yellow = (high[i] >= yellow_line[i]) and (high_prev[i] < yellow_prev[i])
            touch_obl = (high[i] >= orange_band_lower[i]) and (high_prev[i] < obl_prev[i])
            touch_orange = (high[i] >= orange_line[i]) and (high_prev[i] < orange_prev[i])

            # Handle NaN in shifted values — treat as no touch
            if np.isnan(low_prev[i]) or np.isnan(cyan_prev[i]):
                touch_cyan = False
                touch_bbu = False
                touch_blue = False
            if np.isnan(high_prev[i]) or np.isnan(yellow_prev[i]):
                touch_yellow = False
                touch_obl = False
                touch_orange = False

            # Raw entry signals
            long_pullback_ok = _pullback_entry_gate(
                ts_p,
                ts,
                is_long=True,
                strict_pullback_trend_filter=self._strict_pullback_trend_filter,
                compress_l1_signals=False,
                is_l1_level=False,
            )
            short_pullback_ok = _pullback_entry_gate(
                ts_p,
                ts,
                is_long=False,
                strict_pullback_trend_filter=self._strict_pullback_trend_filter,
                compress_l1_signals=False,
                is_l1_level=False,
            )
            l1_long_ok = _pullback_entry_gate(
                ts_p,
                ts,
                is_long=True,
                strict_pullback_trend_filter=self._strict_pullback_trend_filter,
                compress_l1_signals=self._compress_l1_signals,
                is_l1_level=True,
            )
            l1_short_ok = _pullback_entry_gate(
                ts_p,
                ts,
                is_long=False,
                strict_pullback_trend_filter=self._strict_pullback_trend_filter,
                compress_l1_signals=self._compress_l1_signals,
                is_l1_level=True,
            )

            raw_l1 = l1_long_ok and touch_cyan
            raw_l2 = long_pullback_ok and touch_bbu
            raw_l3 = long_pullback_ok and touch_blue

            raw_s1 = l1_short_ok and touch_yellow
            raw_s2 = short_pullback_ok and touch_obl
            raw_s3 = short_pullback_ok and touch_orange

            # L4/S4: crossover/crossunder
            raw_l4 = (close[i] > orange_line[i]) and (close_prev[i] <= orange_prev[i])
            raw_s4 = (close[i] < blue_line[i]) and (close_prev[i] >= blue_prev[i])

            # Handle NaN in close_prev / line_prev
            if np.isnan(close_prev[i]) or np.isnan(orange_prev[i]):
                raw_l4 = False
            if np.isnan(close_prev[i]) or np.isnan(blue_prev[i]):
                raw_s4 = False

            # L4/S4 close
            raw_l4_close = (close[i] < mid_line[i]) and (close_prev[i] >= mid_prev[i])
            raw_s4_close = (close[i] > mid_line[i]) and (close_prev[i] <= mid_prev[i])
            if np.isnan(close_prev[i]) or np.isnan(mid_prev[i]):
                raw_l4_close = False
                raw_s4_close = False

            # ALLSL
            raw_all_long_sl = (close[i] < blue_line[i]) and bear_trend
            raw_all_short_sl = (close[i] > orange_line[i]) and bull_trend

            # --- 5e: Bar-start state snapshot ---
            l1_was = l1_open
            l2_was = l2_open
            l3_was = l3_open
            l4_was = l4_open
            s1_was = s1_open
            s2_was = s2_open
            s3_was = s3_open
            s4_was = s4_open

            # --- 5f: Exit logic (on snapshot) ---
            all_long_exit = raw_all_long_sl and (l1_was or l2_was or l3_was)
            all_short_exit = raw_all_short_sl and (s1_was or s2_was or s3_was)

            l1_tp_hit = (not all_long_exit) and l1_was and (high[i] >= yellow_line[i])
            l2_tp_hit = (not all_long_exit) and l2_was and (high[i] >= orange_band_lower[i])
            l3_tp_hit = (not all_long_exit) and l3_was and (high[i] >= orange_line[i])

            s1_tp_hit = (not all_short_exit) and s1_was and (low[i] <= cyan_line[i])
            s2_tp_hit = (not all_short_exit) and s2_was and (low[i] <= blue_band_upper[i])
            s3_tp_hit = (not all_short_exit) and s3_was and (low[i] <= blue_line[i])

            l4_close_hit = l4_was and raw_l4_close
            s4_close_hit = s4_was and raw_s4_close

            # --- 5g: Entry logic (on snapshot, with cooldown + priority) ---
            cd_l1 = (i - last_l1) > cooldown
            cd_l2 = (i - last_l2) > cooldown
            cd_l3 = (i - last_l3) > cooldown
            cd_l4 = (i - last_l4) > cooldown
            cd_s1 = (i - last_s1) > cooldown
            cd_s2 = (i - last_s2) > cooldown
            cd_s3 = (i - last_s3) > cooldown
            cd_s4 = (i - last_s4) > cooldown

            base_l4 = raw_l4 and (not l4_was) and cd_l4 and (not all_short_exit)
            base_s4 = raw_s4 and (not s4_was) and cd_s4 and (not all_long_exit)

            base_l3 = raw_l3 and (not l3_was) and cd_l3 and (not all_short_exit) and (not base_s4)
            base_l2 = raw_l2 and (not l2_was) and cd_l2 and (not all_short_exit) and (not base_s4)
            base_l1 = raw_l1 and (not l1_was) and cd_l1 and (not all_short_exit) and (not base_s4)

            base_s3 = raw_s3 and (not s3_was) and cd_s3 and (not all_long_exit) and (not base_l4)
            base_s2 = raw_s2 and (not s2_was) and cd_s2 and (not all_long_exit) and (not base_l4)
            base_s1 = raw_s1 and (not s1_was) and cd_s1 and (not all_long_exit) and (not base_l4)

            # Priority: L4 > L3 > L2 > L1
            can_l4 = base_l4
            can_l3 = (not can_l4) and base_l3
            can_l2 = (not can_l4) and (not can_l3) and base_l2
            can_l1 = (not can_l4) and (not can_l3) and (not can_l2) and base_l1

            can_s4 = base_s4
            can_s3 = (not can_s4) and base_s3
            can_s2 = (not can_s4) and (not can_s3) and base_s2
            can_s1 = (not can_s4) and (not can_s3) and (not can_s2) and base_s1

            # --- 5h: Write output ---
            all_long_sl[i] = all_long_exit
            all_short_sl[i] = all_short_exit

            long1_tp[i] = l1_tp_hit
            long2_tp[i] = l2_tp_hit
            long3_tp[i] = l3_tp_hit
            short1_tp[i] = s1_tp_hit
            short2_tp[i] = s2_tp_hit
            short3_tp[i] = s3_tp_hit
            long4_close[i] = l4_close_hit
            short4_close[i] = s4_close_hit

            long1[i] = can_l1
            long2[i] = can_l2
            long3[i] = can_l3
            long4[i] = can_l4
            short1[i] = can_s1
            short2[i] = can_s2
            short3[i] = can_s3
            short4[i] = can_s4

            # --- 5i: Update state ---
            l1_open = (l1_was and (not all_long_exit) and (not l1_tp_hit)) or can_l1
            l2_open = (l2_was and (not all_long_exit) and (not l2_tp_hit)) or can_l2
            l3_open = (l3_was and (not all_long_exit) and (not l3_tp_hit)) or can_l3
            l4_open = (l4_was and (not l4_close_hit)) or can_l4

            s1_open = (s1_was and (not all_short_exit) and (not s1_tp_hit)) or can_s1
            s2_open = (s2_was and (not all_short_exit) and (not s2_tp_hit)) or can_s2
            s3_open = (s3_was and (not all_short_exit) and (not s3_tp_hit)) or can_s3
            s4_open = (s4_was and (not s4_close_hit)) or can_s4

            if can_l1:
                last_l1 = i
            if can_l2:
                last_l2 = i
            if can_l3:
                last_l3 = i
            if can_l4:
                last_l4 = i
            if can_s1:
                last_s1 = i
            if can_s2:
                last_s2 = i
            if can_s3:
                last_s3 = i
            if can_s4:
                last_s4 = i

        # ==================================================================
        # Build output
        # ==================================================================
        idx = data.index

        channels = pd.DataFrame(
            {
                "orange_line": orange_line,
                "blue_line": blue_line,
                "yellow_line": yellow_line,
                "cyan_line": cyan_line,
                "mid_line": mid_line,
                "orange_band_upper": orange_band_upper,
                "orange_band_lower": orange_band_lower,
                "blue_band_upper": blue_band_upper,
                "blue_band_lower": blue_band_lower,
                "trend_state": trend_state,
                "vov_price": vov_price,
                "band_vov_price": band_vov_price,
                # Entry signals
                "long1": long1,
                "long2": long2,
                "long3": long3,
                "long4": long4,
                "short1": short1,
                "short2": short2,
                "short3": short3,
                "short4": short4,
                # TP / close / SL
                "long1_tp": long1_tp,
                "long2_tp": long2_tp,
                "long3_tp": long3_tp,
                "long4_close": long4_close,
                "short1_tp": short1_tp,
                "short2_tp": short2_tp,
                "short3_tp": short3_tp,
                "short4_close": short4_close,
                "all_long_sl": all_long_sl,
                "all_short_sl": all_short_sl,
            },
            index=idx,
        )

        return SignalFrame(
            name=self.name,
            signal_type=SignalType.GRADE,
            values=pd.Series(trend_state.astype(float), index=idx, name="trend_state"),
            metadata={"channels": channels},
        )
