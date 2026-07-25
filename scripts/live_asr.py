"""端到端实时测试：soundcard 采集系统声音 + FunASR 流式转写，实时打印。

用法：
  python scripts/live_asr.py

跑起来后，去播放任意中文视频/音频，会看到实时识别出的中文。
Ctrl+C 停止。
"""
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np
from subtitle.config import load_config
from subtitle.asr import FunAsrEngine
from subtitle.audio import SystemAudioCapture


def main():
    cfg = load_config()
    target_sr = cfg.audio.target_sample_rate
    chunk_samples = int(round(cfg.audio.chunk_seconds * target_sr))

    print("=== 加载 FunASR 模型 ===")
    engine = FunAsrEngine(cfg.asr)
    engine.load()

    print("\n=== 启动系统声音采集 ===")
    cap = SystemAudioCapture(
        target_sr=target_sr,
        block_samples=chunk_samples,
        speaker_name=cfg.audio.input_device,
    )
    cap.start()
    while cap.actual_sr is None:
        if cap.error:
            print(f"[FAIL] {cap.error}")
            return
        time.sleep(0.05)

    print("\n=== 实时识别中（去播放中文声音，Ctrl+C 停止）===\n")
    buf = np.zeros(0, dtype=np.float32)
    try:
        while True:
            try:
                raw = cap.queue.get(timeout=0.5)
            except Exception:
                continue
            buf = np.concatenate([buf, raw])
            while len(buf) >= chunk_samples:
                block = buf[:chunk_samples]
                buf = buf[chunk_samples:]
                r = engine.transcribe_chunk(block, is_final=False)
                if r and r.text:
                    print(r.text, end="", flush=True)
    except KeyboardInterrupt:
        print("\n\n[停止]")
    finally:
        cap.stop()


if __name__ == "__main__":
    main()
