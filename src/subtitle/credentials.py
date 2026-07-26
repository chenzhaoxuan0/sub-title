"""凭证管理 —— 用系统级保险箱存 API key 等敏感信息。

**绝不**把 AccessKey / Secret / AppKey 写进 config.yaml，避免：
1. 不小心 `git add .` 提交到 GitHub
2. 用户截图 / 分享 config.yaml 时泄露
3. 备份软件 / 文件同步时泄露

底层依赖 `keyring` 库，跨平台对应：

| OS       | 底层                                                          |
|----------|--------------------------------------------------------------|
| Windows  | Windows Credential Manager（系统级加密，登录账号绑定）       |
| macOS    | Keychain（系统级加密，用户登录密码保护）                    |
| Linux    | Secret Service（libsecret / KWallet）                        |

fallback：如果 keyring 不可用（比如 Linux 没装 libsecret），退化到
`<user_config_dir>/credentials.json`，Unix 上 chmod 600 限 owner 读写。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

SERVICE_NAME = "sub-title"   # keyring 服务名

# Aliyun NLS 凭证 key
KEY_ALIYUN_AK_ID = "aliyun_access_key_id"
KEY_ALIYUN_AK_SECRET = "aliyun_access_key_secret"
KEY_ALIYUN_APPKEY = "aliyun_appkey"

# 默认 fallback 文件名
_FALLBACK_FILE = "credentials.json"


# ============================================================
# keyring 包装
# ============================================================
def _get_keyring():
    try:
        import keyring  # type: ignore
        return keyring
    except ImportError:
        return None


def is_available() -> bool:
    """返回 True 表示系统 keyring 可用。"""
    kr = _get_keyring()
    if kr is None:
        return False
    try:
        # 探测：写一个再读再删
        kr.set_password(SERVICE_NAME, "__probe__", "ok")
        v = kr.get_password(SERVICE_NAME, "__probe__")
        try:
            kr.delete_password(SERVICE_NAME, "__probe__")
        except Exception:
            pass
        return v == "ok"
    except Exception:
        return False


def _get(key: str) -> Optional[str]:
    kr = _get_keyring()
    if kr is None:
        return None
    try:
        return kr.get_password(SERVICE_NAME, key)
    except Exception:
        return None


def _set(key: str, value: str) -> bool:
    """value 空字符串表示删除。返回 True 表示成功。"""
    if not value:
        return _delete(key)
    kr = _get_keyring()
    if kr is None:
        return False
    try:
        kr.set_password(SERVICE_NAME, key, value)
        return True
    except Exception as e:
        print(f"[credentials] keyring 写入失败: {e}")
        return False


def _delete(key: str) -> bool:
    kr = _get_keyring()
    if kr is None:
        return False
    try:
        kr.delete_password(SERVICE_NAME, key)
        return True
    except Exception:
        return False


# ============================================================
# fallback：本地文件（chmod 600）
# ============================================================
def _fallback_path() -> Path:
    from .paths import user_config_dir
    return user_config_dir() / _FALLBACK_FILE


def _fallback_read() -> dict[str, str]:
    fp = _fallback_path()
    if not fp.exists():
        return {}
    try:
        with open(fp, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _fallback_write(data: dict[str, str]) -> bool:
    fp = _fallback_path()
    try:
        fp.parent.mkdir(parents=True, exist_ok=True)
        with open(fp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        # Unix 限权：只有 owner 能读写
        if os.name == "posix":
            os.chmod(fp, 0o600)
        return True
    except Exception as e:
        print(f"[credentials] fallback 写入失败: {e}")
        return False


# ============================================================
# 公开 API：先 keyring，失败再 fallback
# ============================================================
def get(key: str) -> Optional[str]:
    """读凭证。先 keyring，再 fallback 文件。"""
    v = _get(key)
    if v:
        return v
    return _fallback_read().get(key) or None


def set(key: str, value: str) -> bool:
    """写凭证。value 空字符串表示删除。"""
    if _set(key, value):
        # keyring 成功，同步把 fallback 里的也清掉
        data = _fallback_read()
        if key in data:
            data.pop(key, None)
            _fallback_write(data)
        return True
    # keyring 失败 / 不可用 → fallback
    data = _fallback_read()
    if value:
        data[key] = value
    else:
        data.pop(key, None)
    return _fallback_write(data)


def delete(key: str) -> bool:
    """删凭证。"""
    ok = _delete(key)
    data = _fallback_read()
    if key in data:
        data.pop(key, None)
        _fallback_write(data)
    return ok


# ============================================================
# 阿里云 NLS 凭证便捷 API
# ============================================================
def get_aliyun() -> dict[str, str]:
    """一次性读全部 Aliyun 凭证。"""
    return {
        KEY_ALIYUN_AK_ID: get(KEY_ALIYUN_AK_ID) or "",
        KEY_ALIYUN_AK_SECRET: get(KEY_ALIYUN_AK_SECRET) or "",
        KEY_ALIYUN_APPKEY: get(KEY_ALIYUN_APPKEY) or "",
    }


def set_aliyun(ak_id: str = "", ak_secret: str = "", appkey: str = "") -> dict[str, bool]:
    """一次性写全部 Aliyun 凭证。空字符串 = 删除。

    返回每项是否成功。"""
    return {
        KEY_ALIYUN_AK_ID: set(KEY_ALIYUN_AK_ID, ak_id),
        KEY_ALIYUN_AK_SECRET: set(KEY_ALIYUN_AK_SECRET, ak_secret),
        KEY_ALIYUN_APPKEY: set(KEY_ALIYUN_APPKEY, appkey),
    }


def storage_location() -> str:
    """给 UI 显示：当前凭证存在哪。"""
    if _get_keyring() is not None:
        try:
            if is_available():
                if sys.platform == "win32":
                    return "Windows Credential Manager"
                if sys.platform == "darwin":
                    return "macOS Keychain"
                return "系统密钥库（Secret Service）"
        except Exception:
            pass
    return f"本地文件（{_fallback_path()}，chmod 600）"
