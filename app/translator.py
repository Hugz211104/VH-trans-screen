from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Optional

import httpx

from app.config import CACHE_PATH, AppConfig

_SKIP_PATTERN = re.compile(r"^[\d\s\W_]+$", re.UNICODE)
_LINE_PAIR_RE = re.compile(r"^\s*(\d+)\s*\|\s*(.+?)\s*$")
_OCR_HINT = (
    "Lưu ý: văn bản nguồn đọc bằng OCR từ font game nên có thể lẫn 1-2 ký tự sai "
    "(l/I/1, O/0, c/e); hãy tự suy đoán đúng từ gốc theo ngữ cảnh game rồi dịch."
)

GEMINI_FALLBACK_MODELS = (
    "gemini-3.5-flash-lite",
    "gemini-flash-lite-latest",
    "gemini-3.1-flash-lite",
    "gemini-3.8-flash",
)

try:
    import h2 as _h2  # noqa: F401
    _HTTP2_AVAILABLE = True
except Exception:
    _HTTP2_AVAILABLE = False


def _should_skip_text(text: str) -> bool:
    stripped = text.strip()
    return not stripped or bool(_SKIP_PATTERN.match(stripped))


def _normalize_text(text: str) -> str:
    return " ".join(text.strip().split())


def _extract_json_object(raw: str) -> dict[str, str]:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        text = text[start : end + 1]
    data = json.loads(text)
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def _parse_pipe_or_json(raw: str, count: int) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for line in raw.splitlines():
        m = _LINE_PAIR_RE.match(line)
        if m:
            mapping[m.group(1)] = m.group(2).strip()
    if mapping:
        return mapping
    try:
        return _extract_json_object(raw)
    except Exception:
        pass
    if count == 1 and raw.strip():
        return {"0": raw.strip().splitlines()[0].strip()}
    return {}


def _format_api_error(resp: httpx.Response, prefix: str) -> str:
    detail = resp.text[:260]
    try:
        ej = resp.json()
        if isinstance(ej, dict) and isinstance(ej.get("error"), dict):
            e = ej["error"]
            status = e.get("status", "")
            msg = e.get("message", detail)
            detail = f"{status} - {msg}" if status else str(msg)
    except Exception:
        pass
    return f"HTTP {resp.status_code} ({prefix}): {detail}"


def resolve_openai_base_url(config: AppConfig) -> str:
    """Resolve OpenAI-compatible base URL, auto-routing Groq gsk_ and Cerebras csk- keys."""
    base = (config.openai_base_url.strip() or "https://api.openai.com/v1").rstrip("/")
    key = config.openai_api_key.strip()
    if "api.openai.com" in base:
        if key.startswith("gsk_"):
            return "https://api.groq.com/openai/v1"
        if key.startswith("csk-"):
            return "https://api.cerebras.ai/v1"
    return base


class TranslationCache:
    """Thread-safe O(1) persistent LRU cache with atomic debounced disk writes."""

    def __init__(self, path: Path = CACHE_PATH, max_entries: int = 15000) -> None:
        self._path = path
        self._max_entries = max_entries
        self._lock = threading.Lock()
        self._data: OrderedDict[str, str] = OrderedDict()
        self._dirty = False
        self._last_save = time.monotonic()
        self.load()

    def load(self) -> None:
        if not self._path.exists():
            return
        try:
            loaded = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                with self._lock:
                    self._data = OrderedDict((str(k), str(v)) for k, v in loaded.items())
        except Exception:
            with self._lock:
                self._data.clear()

    def save(self, force: bool = True) -> None:
        now = time.monotonic()
        with self._lock:
            if not self._dirty:
                return
            if not force and (now - self._last_save) < 8.0:
                return
            while len(self._data) > self._max_entries:
                self._data.popitem(last=False)
            snapshot = json.dumps(self._data, ensure_ascii=False, separators=(",", ":"))
            self._dirty = False
            self._last_save = now

        tmp = self._path.with_suffix(".json.tmp")
        try:
            tmp.write_text(snapshot, encoding="utf-8")
            tmp.replace(self._path)
        except Exception:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self._dirty = True
        self.save(force=True)

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

    @staticmethod
    def _make_key(provider: str, target_lang: str, norm_text: str) -> str:
        return f"{provider}|{target_lang}|{norm_text}"

    def get(self, provider: str, target_lang: str, text: str) -> Optional[str]:
        norm = _normalize_text(text)
        key = self._make_key(provider, target_lang, norm)
        with self._lock:
            val = self._data.get(key)
            if val is not None:
                self._data.move_to_end(key)
                return val
            trimmed = norm.strip(".,:;!?•·|\"' ")
            if trimmed and trimmed != norm:
                tkey = self._make_key(provider, target_lang, trimmed)
                val = self._data.get(tkey)
                if val is not None:
                    self._data.move_to_end(tkey)
                    return val
        return None

    def put(self, provider: str, target_lang: str, text: str, translated: str) -> None:
        key = self._make_key(provider, target_lang, _normalize_text(text))
        with self._lock:
            self._data[key] = translated
            self._data.move_to_end(key)
            if len(self._data) > self._max_entries:
                self._data.popitem(last=False)
            self._dirty = True


async def list_gemini_models_async(api_key: str) -> list[str]:
    key = api_key.strip()
    if not key:
        raise ValueError("Chưa nhập Gemini API Key.")
    async with httpx.AsyncClient(http2=_HTTP2_AVAILABLE, timeout=15.0) as client:
        resp = await client.get(
            "https://generativelanguage.googleapis.com/v1beta/models",
            headers={"x-goog-api-key": key},
        )
        if resp.status_code != 200:
            raise RuntimeError(_format_api_error(resp, "Gemini Models"))
        data = resp.json()

    skip_tags = ("tts", "image", "embedding", "aqa", "robotics", "computer-use")
    models = [
        name
        for item in data.get("models", [])
        if "generateContent" in item.get("supportedGenerationMethods", [])
        and (name := item.get("name", "").replace("models/", ""))
        and not any(s in name.lower() for s in skip_tags)
    ]
    models.sort(key=lambda m: (0 if "flash-lite" in m else (1 if "flash" in m else 2), m))
    return models


class MultiApiTranslator:
    """HTTP/2 Keep-Alive deduplicated batch translator for Google Free, Gemini, and OpenAI/Groq/Cerebras."""

    def __init__(self, cache: Optional[TranslationCache] = None) -> None:
        self.cache = cache or TranslationCache()
        self.last_used_model: str = ""
        self._google_client: Optional[httpx.AsyncClient] = None
        self._llm_client: Optional[httpx.AsyncClient] = None

    def _get_google_client(self) -> httpx.AsyncClient:
        if self._google_client is None or self._google_client.is_closed:
            self._google_client = httpx.AsyncClient(
                http2=_HTTP2_AVAILABLE,
                timeout=6.5,
                follow_redirects=True,
                limits=httpx.Limits(max_keepalive_connections=12, keepalive_expiry=180.0),
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/131.0.0.0 Safari/537.36"
                    )
                },
            )
        return self._google_client

    def _get_llm_client(self) -> httpx.AsyncClient:
        if self._llm_client is None or self._llm_client.is_closed:
            self._llm_client = httpx.AsyncClient(
                http2=_HTTP2_AVAILABLE,
                timeout=11.0,
                limits=httpx.Limits(max_keepalive_connections=10, keepalive_expiry=180.0),
            )
        return self._llm_client

    async def aclose(self) -> None:
        self.cache.save(force=True)
        for client in (self._google_client, self._llm_client):
            if client is not None and not client.is_closed:
                try:
                    await client.aclose()
                except Exception:
                    pass
        self._google_client = None
        self._llm_client = None

    async def warmup_async(self, config: AppConfig) -> None:
        """Pre-warm HTTP/2 TLS connections so the first game frame translates in ~45 ms."""
        tasks = []
        if config.provider == "google_free" or config.hybrid_fast_preview:
            async def _warm_google() -> None:
                try:
                    await self._get_google_client().get(
                        "https://clients5.google.com/translate_a/t",
                        params=[
                            ("client", "dict-chrome-ex"),
                            ("sl", "en"),
                            ("tl", config.target_language or "vi"),
                            ("q", "OK"),
                        ],
                    )
                except Exception:
                    pass
            tasks.append(_warm_google())
        if config.provider == "gemini" and config.gemini_api_key.strip():
            async def _warm_gemini() -> None:
                try:
                    await self._get_llm_client().get(
                        "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite",
                        headers={"x-goog-api-key": config.gemini_api_key.strip()},
                    )
                except Exception:
                    pass
            tasks.append(_warm_gemini())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def check_cache(
        self, texts: list[str], config: AppConfig, fallback_google: bool = False
    ) -> tuple[list[Optional[str]], dict[str, list[int]]]:
        """Resolve cached entries and deduplicate uncached strings in one pass."""
        results: list[Optional[str]] = [None] * len(texts)
        unique_missing: dict[str, list[int]] = {}

        for idx, text in enumerate(texts):
            if _should_skip_text(text):
                results[idx] = text
                continue
            if config.use_cache:
                hit = self.cache.get(config.provider, config.target_language, text)
                if hit is None and fallback_google and config.provider != "google_free":
                    hit = self.cache.get("google_free", config.target_language, text)
                if hit is not None:
                    results[idx] = hit
                    continue
            unique_missing.setdefault(_normalize_text(text), []).append(idx)
        return results, unique_missing

    def _apply_and_cache(
        self,
        texts: list[str],
        results: list[Optional[str]],
        unique_missing: dict[str, list[int]],
        translated_list: list[str],
        cache_provider: str,
        config: AppConfig,
    ) -> list[str]:
        for src, dst in zip(unique_missing.keys(), translated_list):
            final = dst.strip() if dst and dst.strip() else src
            for idx in unique_missing[src]:
                results[idx] = final
            if config.use_cache and final != src:
                self.cache.put(cache_provider, config.target_language, src, final)
        if config.use_cache:
            self.cache.save(force=False)
        return [r if r is not None else t for r, t in zip(results, texts)]

    async def translate_fast_google_async(
        self, texts: list[str], config: AppConfig
    ) -> list[str]:
        if not texts:
            return []
        results, unique_missing = self.check_cache(texts, config, fallback_google=True)
        if not unique_missing:
            return [r if r is not None else t for r, t in zip(results, texts)]
        translated = await self._translate_google_free(
            list(unique_missing.keys()), config.target_language
        )
        return self._apply_and_cache(
            texts, results, unique_missing, translated, "google_free", config
        )

    async def translate_batch_async(
        self, texts: list[str], config: AppConfig
    ) -> list[str]:
        if not texts:
            return []
        results, unique_missing = self.check_cache(texts, config)
        if not unique_missing:
            return [r if r is not None else t for r, t in zip(results, texts)]

        uniq_texts = list(unique_missing.keys())
        if config.provider == "gemini":
            translated = await self._translate_gemini(uniq_texts, config)
        elif config.provider == "openai":
            translated = await self._translate_openai(uniq_texts, config)
        else:
            translated = await self._translate_google_free(
                uniq_texts, config.target_language
            )
        return self._apply_and_cache(
            texts, results, unique_missing, translated, config.provider, config
        )

    async def _translate_google_chunk(
        self, client: httpx.AsyncClient, chunk: list[str], target_lang: str
    ) -> list[str]:
        params = [
            ("client", "dict-chrome-ex"),
            ("sl", "auto"),
            ("tl", target_lang),
            *(("q", t) for t in chunk),
        ]
        resp = await client.get("https://clients5.google.com/translate_a/t", params=params)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list) and len(data) == len(chunk):
            return [
                item[0] if isinstance(item, list) and item and isinstance(item[0], str)
                else (item if isinstance(item, str) else orig)
                for item, orig in zip(data, chunk)
            ]
        return chunk

    async def _translate_google_free(
        self, texts: list[str], target_lang: str
    ) -> list[str]:
        client = self._get_google_client()
        chunks = [texts[i : i + 18] for i in range(0, len(texts), 18)]
        try:
            chunk_results = await asyncio.gather(
                *(self._translate_google_chunk(client, c, target_lang) for c in chunks)
            )
            return [item for sub in chunk_results for item in sub]
        except Exception:
            async def _one(text: str) -> str:
                resp = await client.get(
                    "https://translate.googleapis.com/translate_a/single",
                    params={
                        "client": "gtx", "sl": "auto", "tl": target_lang, "dt": "t", "q": text,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                if isinstance(data, list) and data and isinstance(data[0], list):
                    return "".join(part[0] for part in data[0] if part and part[0])
                return text

            return list(await asyncio.gather(*(_one(t) for t in texts)))

    async def _translate_gemini(
        self, texts: list[str], config: AppConfig
    ) -> list[str]:
        api_key = config.gemini_api_key.strip()
        if not api_key:
            raise ValueError("Chưa nhập Gemini / Vertex AI API Key trong Cài đặt.")

        primary_model = config.gemini_model.strip() or "gemini-3.5-flash-lite"
        candidates = [primary_model, *(m for m in GEMINI_FALLBACK_MODELS if m != primary_model)]
        lines_in = "\n".join(f"{i}|{t.replace(chr(10), ' ')}" for i, t in enumerate(texts))
        est_tokens = max(96, min(1536, sum(len(t) for t in texts) * 2))
        prompt = (
            f"{config.game_context}\n{_OCR_HINT}\n"
            f"Dịch sang {config.target_language}. Trả về đúng định dạng mỗi dòng 'số|bản dịch', không giải thích:\n"
            f"{lines_in}"
        )
        headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}
        endpoints = [
            ("AI Studio", "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"),
        ]
        if api_key.startswith("AQ."):
            endpoints.append(
                ("Vertex AI Express", "https://aiplatform.googleapis.com/v1/publishers/google/models/{model}:generateContent")
            )

        client = self._get_llm_client()
        last_err: Optional[str] = None
        for ep_name, ep_tpl in endpoints:
            ep_auth_failed = False
            for m_idx, model in enumerate(candidates):
                url = ep_tpl.format(model=model)
                gen_cfg: dict = {"temperature": 0.1, "maxOutputTokens": est_tokens}
                if "flash-lite" in model.lower():
                    gen_cfg["thinkingConfig"] = {"thinkingLevel": "minimal"}
                body = {
                    "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                    "generationConfig": gen_cfg,
                }
                max_attempts = 2 if m_idx == 0 else 1
                for attempt in range(max_attempts):
                    try:
                        resp = await client.post(url, headers=headers, json=body)
                        if resp.status_code == 400 and "thinkingConfig" in gen_cfg:
                            gen_cfg.pop("thinkingConfig", None)
                            resp = await client.post(url, headers=headers, json=body)
                    except httpx.TimeoutException:
                        last_err = f"Timeout ({ep_name} / {model})"
                        continue

                    if resp.status_code == 200:
                        raw_text = (
                            resp.json()
                            .get("candidates", [{}])[0]
                            .get("content", {})
                            .get("parts", [{}])[0]
                            .get("text", "")
                        )
                        mapping = _parse_pipe_or_json(raw_text, len(texts))
                        self.last_used_model = model
                        return [mapping.get(str(i), t) for i, t in enumerate(texts)]

                    last_err = _format_api_error(resp, f"{ep_name} / {model}")
                    if resp.status_code in (401, 403):
                        ep_auth_failed = True
                        break
                    if resp.status_code == 404:
                        break
                    if resp.status_code in (429, 500, 503):
                        if attempt + 1 < max_attempts:
                            await asyncio.sleep(0.45)
                            continue
                        break
                    raise RuntimeError(last_err)
                if ep_auth_failed:
                    break
            if not ep_auth_failed:
                break

        raise RuntimeError(last_err or "Không thể kết nối Gemini API.")

    async def _translate_openai(
        self, texts: list[str], config: AppConfig
    ) -> list[str]:
        api_key = config.openai_api_key.strip()
        base_url = resolve_openai_base_url(config)
        model = config.openai_model.strip() or "gpt-4o-mini"

        if not api_key and "localhost" not in base_url and "127.0.0.1" not in base_url:
            raise ValueError("Chưa nhập OpenAI / Groq / Cerebras API Key trong Cài đặt.")

        lines_in = "\n".join(f"{i}|{t.replace(chr(10), ' ')}" for i, t in enumerate(texts))
        system_msg = (
            f"{config.game_context}\n{_OCR_HINT}\n"
            f"Dịch sang {config.target_language}. Trả về đúng định dạng mỗi dòng 'số|bản dịch', không giải thích."
        )
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        resp = await self._get_llm_client().post(
            f"{base_url}/chat/completions",
            headers=headers,
            json={
                "model": model,
                "temperature": 0.1,
                "messages": [
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": lines_in},
                ],
            },
        )
        if resp.status_code != 200:
            raise RuntimeError(_format_api_error(resp, model))

        raw_text = (
            resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
        )
        mapping = _parse_pipe_or_json(raw_text, len(texts))
        self.last_used_model = model
        return [mapping.get(str(i), t) for i, t in enumerate(texts)]
