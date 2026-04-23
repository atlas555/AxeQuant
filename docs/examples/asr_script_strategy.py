# ASR Strategy — QD ScriptStrategy form
#
# Uses backTestSys asrband signal via ctx.signal().
# All signal math runs in vendored plugin code (outside safe_exec sandbox).
# This script is thin: it only makes trading decisions from per-bar signal values.
#
# Parity guarantee: ctx.signal("asrband", **params) returns the same values
# as running SignalRegistry.create("asrband", **params).compute(df) directly.

def on_init(ctx):
    ctx.param("asr_length", 94)
    ctx.param("channel_width", 6.5)
    ctx.param("band_mult", 0.22)
    ctx.param("ewm_halflife", 178)
    ctx.param("cooldown_bars", 8)
    ctx.param("risk_per_trade", 0.01)
    ctx.param("leverage", 3)


def on_bar(ctx):
    bar = ctx.bars(1)[0]
    price = bar.close

    asr = ctx.signal(
        "asrband",
        asr_length=ctx.param("asr_length"),
        channel_width=ctx.param("channel_width"),
        band_mult=ctx.param("band_mult"),
        ewm_halflife=ctx.param("ewm_halflife"),
        cooldown_bars=ctx.param("cooldown_bars"),
    )

    size = float(ctx.position.get("size") or 0.0)
    side = ctx.position.get("side")

    if size == 0:
        if asr.long1 or asr.long2 or asr.long3 or asr.long4:
            notional = ctx.balance * ctx.param("risk_per_trade") * ctx.param("leverage")
            amount = notional / price
            ctx.buy(price=price, amount=amount)
        elif asr.short1 or asr.short2 or asr.short3 or asr.short4:
            notional = ctx.balance * ctx.param("risk_per_trade") * ctx.param("leverage")
            amount = notional / price
            ctx.sell(price=price, amount=amount)
        return

    if side == "long":
        if asr.long1_tp or asr.long2_tp or asr.long3_tp or asr.long4_close or asr.all_long_sl:
            ctx.close_position()
    elif side == "short":
        if asr.short1_tp or asr.short2_tp or asr.short3_tp or asr.short4_close or asr.all_short_sl:
            ctx.close_position()
