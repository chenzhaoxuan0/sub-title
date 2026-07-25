"""用 soundcard 库做 loopback 测试（最可靠）。

soundcard 原生支持 Windows WASAPI loopback：default_speaker().recorder()
录到的就是系统输出的声音。

录 N 秒存 rec.wav，打印 RMS。
"""
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np
import soundfile as sf
import soundcard as sc


def main(secs=5, out="rec.wav", target_sr=16000):
    # 默认扬声器 = 系统默认输出；对它 recorder 录到的就是 loopback
    speaker = sc.default_speaker()
    print(f"[soundcard] 默认输出: {speaker.name}")

    # 用麦克风 API 录扬声器（loopback 的核心技巧）
    # soundcard 里 get_microboard 能拿到 loopback 设备
    mics = sc.all_microphones(include_loopback=True)
    loopback = None
    for m in mics:
        if m.isloopback and speaker.name in (m.name or ""):
            loopback = m
            break
    if loopback is None:
        # 退而求其次：任意 loopback
        for m in mics:
            if getattr(m, "isloopback", False):
                loopback = m
                break
    if loopback is None:
        print("[FAIL] 没找到 loopback 设备")
        print("all mics:", [(m.name, getattr(m, "isloopback", False)) for m in mics])
        return

    print(f"[soundcard] loopback 设备: {loopback.name}")

    print(f"[soundcard] 录 {secs}s，请播放声音...")
    with loopback.recorder(samplerate=target_sr, channels=1) as rec:
        data = rec.record(numframes=int(secs * target_sr))

    data = data.flatten().astype(np.float32)
    rms = float(np.sqrt(np.mean(data ** 2))) if len(data) else 0
    print(f"\n[done] 录到 {len(data)/target_sr:.1f}s, RMS={rms:.4f}")
    sf.write(out, data, target_sr)
    print(f"[saved] {out} ({target_sr}Hz mono)")
    if rms < 0.001:
        print("[warn] 能量低，确认有声音在播放")
    else:
        print("[ok] 成功录到系统声音！")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--secs", type=int, default=5)
    p.add_argument("--out", type=str, default="rec.wav")
    p.add_argument("--sr", type=int, default=16000)
    a = p.parse_args()
    main(a.secs, a.out, a.sr)
