"""调试/验证脚本 —— 独立于 GUI。

用法：
  python scripts/test_capture.py --list                 列出音频设备
  python scripts/test_capture.py --record 5             录 5 秒系统声音存 captured.wav
  python scripts/test_capture.py --play captured.wav    回放（确认录到了系统声音）
  python scripts/test_capture.py --asr captured.wav     对 wav 跑流式转写
  python scripts/test_capture.py --live                  实时采集+转写（无 GUI，验证整链路）
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

# 让 src/subtitle 可被 import
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def cmd_list(args):
    from subtitle.audio import list_output_devices
    devs = list_output_devices()
    if not devs:
        print("没找到任何输出设备。")
        return
    print(f"共 {len(devs)} 个输出设备（可用于 loopback 捕获）：")
    for d in devs:
        print(f"  [{d.index:2d}] {d.name}  ({d.hostapi}, {d.max_output_channels}ch, {d.default_samplerate:.0f}Hz)")
    print("\n把想要的设备名或 index 填进 config.yaml 的 audio.input_device。")


def cmd_record(args):
    import sounddevice as sd
    import soundfile as sf
    secs = args.record
    sr = args.sr
    print(f"录制 {secs} 秒系统声音... (现在去播放点声音)")
    rec = sd.rec(int(secs * sr), samplerate=sr, channels=1, dtype="float32",
                 device=args.device, mapping=None)
    sd.wait()
    out = Path(args.out)
    sf.write(str(out), rec, sr)
    print(f"已保存：{out} (形状={rec.shape})")
    # 简单能量检查
    energy = float(np.sqrt(np.mean(rec ** 2)))
    print(f"RMS 能量 = {energy:.4f}  (太低可能没录到系统声音)")


def cmd_play(args):
    import sounddevice as sd
    import soundfile as sf
    data, sr = sf.read(args.play, dtype="float32")
    print(f"回放 {args.play} ({len(data)/sr:.1f}s)")
    sd.play(data, sr)
    sd.wait()


def cmd_asr(args):
    """对 wav 跑流式转写 —— 阶段1 模型冒烟。"""
    import soundfile as sf
    from subtitle.config import load_config
    from subtitle.asr import FunAsrEngine

    # 支持 --asr auto：自动在 test/ 目录找第一个 wav
    target = args.asr
    if target == "auto":
        test_dir = Path(__file__).resolve().parents[1] / "test"
        wavs = sorted(test_dir.glob("*.wav")) if test_dir.exists() else []
        if not wavs:
            print(f"[ERROR] no wav under {test_dir}")
            return
        target = str(wavs[0])
        print(f"[auto] using {target}")

    cfg = load_config()
    cfg.asr.device = args.device or cfg.asr.device
    engine = FunAsrEngine(cfg.asr)
    engine.load()

    data, sr = sf.read(target, dtype="float32")
    if data.ndim > 1:
        data = data.mean(axis=1)
    data = data.astype(np.float32)
    # 重采样到 16k
    if sr != cfg.audio.target_sample_rate:
        from subtitle.audio.resample import resample
        data = resample(data, sr, cfg.audio.target_sample_rate)
        sr = cfg.audio.target_sample_rate

    chunk_stride = int(cfg.asr.chunk_size[1] * 960)  # 9600 @16k
    print(f"\n流式转写（chunk_stride={chunk_stride}, {chunk_stride/sr*1000:.0f}ms）...\n")
    n = (len(data) - 1) // chunk_stride + 1
    full = ""
    for i in range(n):
        block = data[i * chunk_stride : (i + 1) * chunk_stride]
        if len(block) == 0:
            continue
        r = engine.transcribe_chunk(block, is_final=(i == n - 1))
        if r and r.text:
            full += r.text
            print(r.text, end="", flush=True)
    print(f"\n\n[完成] 全文：\n{full}")


def cmd_live(args):
    """实时采集 + 转写 —— 阶段3 管线验证。"""
    from subtitle.config import load_config
    from subtitle.asr import FunAsrEngine
    from subtitle.pipeline import SubtitlePipeline

    cfg = load_config()
    cfg.asr.device = args.device or cfg.asr.device
    engine = FunAsrEngine(cfg.asr)
    pipe = SubtitlePipeline(cfg, engine, on_text=lambda t, f: print(t, end="", flush=True))
    print("开始实时识别（Ctrl+C 停止）...")
    pipe.start()
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n停止。")
        pipe.stop()


def main():
    p = argparse.ArgumentParser(description="音频捕获/ASR 调试脚本")
    p.add_argument("--list", action="store_true", help="列出音频设备")
    p.add_argument("--record", type=float, metavar="SEC", help="录指定秒数系统声音")
    p.add_argument("--play", type=str, metavar="WAV", help="回放 wav")
    p.add_argument("--asr", type=str, metavar="WAV", help="对 wav 跑流式转写")
    p.add_argument("--live", action="store_true", help="实时采集+转写")
    p.add_argument("--device", type=str, default=None, help="设备名/index 或 asr device(cuda/cpu)")
    p.add_argument("--sr", type=int, default=16000, help="录制采样率")
    p.add_argument("--out", type=str, default="captured.wav", help="录制输出文件名")
    args = p.parse_args()

    if args.list:
        cmd_list(args)
    elif args.record is not None:
        cmd_record(args)
    elif args.play:
        cmd_play(args)
    elif args.asr:
        cmd_asr(args)
    elif args.live:
        cmd_live(args)
    else:
        p.print_help()


if __name__ == "__main__":
    main()
