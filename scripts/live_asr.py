"""端到端实时测试：soundcard 采集系统声音 + ASR 引擎实时转写，实时打印。

用法：
  python scripts/live_asr.py

引擎由 config.yaml 的 asr.engine_type 决定（funasr/sensevoice/aliyun）。
跑起来后去播放任意中文视频/音频，会看到实时识别出的中文。Ctrl+C 停止。
"""
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np
from subtitle.config import load_config
from subtitle.asr import create_engine
from subtitle.audio import SystemAudioCapture


def main():
    cfg = load_config()
    print(f"=== 使用引擎: {cfg.asr.engine_type} ===")
    target_sr = cfg.audio.target_sample_rate
    chunk_samples = int(round(cfg.audio.chunk_seconds * target_sr))

    engine = create_engine(cfg, on_result=lambda t, f: print(t, end="", flush=True))
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
                engine.feed(block)   # 事件驱动：feed 不返回，结果走回调
    except KeyboardInterrupt:
        print("\n\n[停止]")
    finally:
        engine.stop()
        cap.stop()


if __name__ == "__main__":
    main()
