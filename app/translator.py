from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Callable, Optional

import httpx

from app.config import CACHE_PATH, AppConfig

_SKIP_PATTERN = re.compile(r"^[\d\s\W_]+$", re.UNICODE)
_LINE_PAIR_RE = re.compile(r"^\s*(\d+)\s*\|\s*(.+?)\s*$")
_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_THINK_OPEN_RE = re.compile(r"<think>.*$", re.DOTALL | re.IGNORECASE)

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

try:
    from rapidfuzz import fuzz as _rf_fuzz, process as _rf_process
    _RAPIDFUZZ_AVAILABLE = True
except Exception:
    _rf_fuzz = None  # type: ignore
    _rf_process = None  # type: ignore
    _RAPIDFUZZ_AVAILABLE = False

PartialCallback = Callable[[dict[str, str], int], None]


def _should_skip_text(text: str) -> bool:
    stripped = text.strip()
    return not stripped or bool(_SKIP_PATTERN.match(stripped))


def _normalize_text(text: str) -> str:
    return " ".join(text.strip().split())


def _strip_think_tags(raw: str) -> str:
    """Remove Qwen/DeepSeek <think>...</think> reasoning sections (complete or in-progress)."""
    if "<think>" not in raw.lower():
        return raw
    cleaned = _THINK_BLOCK_RE.sub("", raw)
    cleaned = _THINK_OPEN_RE.sub("", cleaned)
    return cleaned


def is_short_ui_text(text: str) -> bool:
    """Detect short 1-4 word UI labels/buttons/items suitable for fast Tier-1 routing."""
    norm = _normalize_text(text)
    if not norm:
        return True
    # CJK scripts without spaces: treat <= 7 chars without sentence punctuation as UI label
    if any("\u3000" <= ch <= "\u9fff" or "\uac00" <= ch <= "\ud7af" for ch in norm):
        return len(norm) <= 7 and not any(p in norm for p in ("。", "？", "！", "：", "…"))
    if len(norm) > 32 or ":" in norm or ";" in norm:
        return False
    words = norm.split()
    return len(words) <= 4


def _ocr_lang_to_google_sl(language_tag: str) -> str:
    low = (language_tag or "").lower()
    if low.startswith("en"):
        return "en"
    if low.startswith("ja"):
        return "ja"
    if low.startswith("ko"):
        return "ko"
    if low.startswith("zh-hant") or low.startswith("zh-tw") or low.startswith("zh-hk"):
        return "zh-TW"
    if low.startswith("zh"):
        return "zh-CN"
    return "auto"


def _build_compact_system_prompt(config: AppConfig, is_qwen3: bool = False) -> str:
    """Fixed static prompt (<75 tokens) optimized for KV-Cache & LLM prefix caching."""
    ctx = _normalize_text(config.game_context or "")
    if len(ctx) > 160:
        ctx = ctx[:157].rstrip() + "..."
    base = (
        f"Dịch game sang {config.target_language or 'vi'} ngắn gọn, tự nhiên. "
        "Tự sửa lỗi nhỏ do OCR. Chỉ trả từng dòng dạng 'số|bản dịch', không giải thích."
    )
    prompt = f"{base} {ctx}".strip() if ctx else base
    if is_qwen3:
        prompt += " /no_think"
    return prompt


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
    cleaned = _strip_think_tags(raw)
    mapping: dict[str, str] = {}
    for line in cleaned.splitlines():
        m = _LINE_PAIR_RE.match(line)
        if m:
            mapping[m.group(1)] = m.group(2).strip()
    if mapping:
        return mapping
    try:
        return _extract_json_object(cleaned)
    except Exception:
        pass
    if count == 1 and cleaned.strip():
        first = cleaned.strip().splitlines()[0].strip()
        if first and not first.startswith("{"):
            return {"0": first}
    return {}


def _format_api_error(resp: httpx.Response, prefix: str, body_text: Optional[str] = None) -> str:
    raw = body_text if body_text is not None else resp.text
    detail = raw[:260]
    try:
        ej = json.loads(raw)
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
    """2-tier thread-safe persistent LRU cache: L1 exact O(1) hash + L2 C++ rapidfuzz (>=93%)."""

    def __init__(
        self,
        path: Path = CACHE_PATH,
        max_entries: int = 15000,
        fuzzy_threshold: float = 93.0,
    ) -> None:
        self._path = path
        self._max_entries = max_entries
        self._fuzzy_threshold = fuzzy_threshold
        self._lock = threading.Lock()
        self._data: OrderedDict[str, str] = OrderedDict()
        # L2 index: (provider, target_lang) -> {norm_text: translated} for len(norm_text) >= 8
        self._fuzzy_buckets: dict[tuple[str, str], dict[str, str]] = {}
        self._dirty = False
        self._last_save = time.monotonic()
        self.load()

    def _index_entry_locked(self, full_key: str, val: str) -> None:
        parts = full_key.split("|", 2)
        if len(parts) == 3 and len(parts[2]) >= 8:
            bucket = self._fuzzy_buckets.setdefault((parts[0], parts[1]), {})
            bucket[parts[2]] = val

    def _unindex_entry_locked(self, full_key: str) -> None:
        parts = full_key.split("|", 2)
        if len(parts) == 3:
            bucket = self._fuzzy_buckets.get((parts[0], parts[1]))
            if bucket is not None:
                bucket.pop(parts[2], None)

    def load(self) -> None:
        if not self._path.exists():
            return
        try:
            loaded = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                with self._lock:
                    self._data = OrderedDict((str(k), str(v)) for k, v in loaded.items())
                    self._fuzzy_buckets.clear()
                    for k, v in self._data.items():
                        self._index_entry_locked(k, v)
        except Exception:
            with self._lock:
                self._data.clear()
                self._fuzzy_buckets.clear()

    def save(self, force: bool = True) -> None:
        now = time.monotonic()
        with self._lock:
            if not self._dirty:
                return
            if not force and (now - self._last_save) < 8.0:
                return
            while len(self._data) > self._max_entries:
                evicted_k, _ = self._data.popitem(last=False)
                self._unindex_entry_locked(evicted_k)
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
            self._fuzzy_buckets.clear()
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
            # Tier 1: O(1) exact hash lookup (~0.002 ms)
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

            # Tier 2: C++ rapidfuzz lookup (< 0.5 ms) for 1-2 char OCR jitter
            if not _RAPIDFUZZ_AVAILABLE or len(norm) < 8:
                return None
            bucket = self._fuzzy_buckets.get((provider, target_lang))
            if not bucket:
                return None
            nlen = len(norm)
            min_l = int(nlen * 0.85)
            max_l = int(nlen * 1.15) + 1
            candidates = [k for k in bucket.keys() if min_l <= len(k) <= max_l]
            if not candidates:
                return None
            match = _rf_process.extractOne(
                norm,
                candidates,
                scorer=_rf_fuzz.ratio,
                score_cutoff=self._fuzzy_threshold,
            )
            if match is not None:
                return bucket.get(match[0])
        return None

    def put(self, provider: str, target_lang: str, text: str, translated: str) -> None:
        norm = _normalize_text(text)
        key = self._make_key(provider, target_lang, norm)
        with self._lock:
            self._data[key] = translated
            self._data.move_to_end(key)
            self._index_entry_locked(key, translated)
            if len(self._data) > self._max_entries:
                evicted_k, _ = self._data.popitem(last=False)
                self._unindex_entry_locked(evicted_k)
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
    """HTTP/2 Keep-Alive deduplicated translator with Tiered Routing + SSE Streaming."""

    def __init__(self, cache: Optional[TranslationCache] = None) -> None:
        self.cache = cache or TranslationCache()
        self.last_used_model: str = ""
        self.last_ttft_ms: int = 0
        self._google_client: Optional[httpx.AsyncClient] = None
        self._llm_client: Optional[httpx.AsyncClient] = None

    def _get_google_client(self) -> httpx.AsyncClient:
        if self._google_client is None or self._google_client.is_closed:
            self._google_client = httpx.AsyncClient(
                http2=_HTTP2_AVAILABLE,
                timeout=6.5,
                follow_redirects=True,
                limits=httpx.Limits(max_keepalive_connections=12, keepalive_expiry=300.0),
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
                timeout=httpx.Timeout(connect=5.0, read=14.0, write=8.0, pool=5.0),
                limits=httpx.Limits(max_keepalive_connections=10, keepalive_expiry=300.0),
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
        """Pre-warm HTTP/2 TLS connections across Google, Gemini, and OpenAI/Groq/Cerebras/Ollama."""
        tasks = []
        sl = _ocr_lang_to_google_sl(config.ocr_language)
        if config.provider == "google_free" or config.hybrid_fast_preview or config.tiered_routing:
            async def _warm_google() -> None:
                try:
                    await self._get_google_client().get(
                        "https://clients5.google.com/translate_a/t",
                        params=[
                            ("client", "dict-chrome-ex"),
                            ("sl", sl if sl != "auto" else "en"),
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
                    model = config.gemini_model.strip() or "gemini-3.5-flash-lite"
                    await self._get_llm_client().get(
                        f"https://generativelanguage.googleapis.com/v1beta/models/{model}",
                        headers={"x-goog-api-key": config.gemini_api_key.strip()},
                    )
                except Exception:
                    pass
            tasks.append(_warm_gemini())
        elif config.provider == "openai":
            async def _warm_openai() -> None:
                try:
                    base = resolve_openai_base_url(config)
                    headers = {}
                    if config.openai_api_key.strip():
                        headers["Authorization"] = f"Bearer {config.openai_api_key.strip()}"
                    await self._get_llm_client().get(f"{base}/models", headers=headers)
                except Exception:
                    pass
            tasks.append(_warm_openai())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def check_cache(
        self, texts: list[str], config: AppConfig, fallback_google: bool = False
    ) -> tuple[list[Optional[str]], dict[str, list[int]]]:
        """Resolve 2-tier cached entries (L1 exact + L2 rapidfuzz) and deduplicate misses."""
        results: list[Optional[str]] = [None] * len(texts)
        unique_missing: dict[str, list[int]] = {}

        for idx, text in enumerate(texts):
            if _should_skip_text(text):
                results[idx] = text
                continue
            if config.use_cache:
                # Short UI items routed to Tier 1 are cached under google_free too
                tier1_short = (
                    config.tiered_routing
                    and config.provider != "google_free"
                    and is_short_ui_text(text)
                )
                hit = self.cache.get(config.provider, config.target_language, text)
                if hit is None and (fallback_google or tier1_short) and config.provider != "google_free":
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
            self.last_ttft_ms = 0
            return [r if r is not None else t for r, t in zip(results, texts)]
        t0 = time.perf_counter()
        translated = await self._translate_google_free(
            list(unique_missing.keys()),
            config.target_language,
            source_lang=_ocr_lang_to_google_sl(config.ocr_language),
        )
        self.last_ttft_ms = int((time.perf_counter() - t0) * 1000)
        return self._apply_and_cache(
            texts, results, unique_missing, translated, "google_free", config
        )

    async def translate_batch_async(
        self,
        texts: list[str],
        config: AppConfig,
        on_partial: Optional[Callable[[list[str], int], None]] = None,
    ) -> list[str]:
        """Translate batch with 2-tier cache, Tiered Routing (short UI -> fast HTTP/2), and SSE streaming."""
        if not texts:
            return []
        results, unique_missing = self.check_cache(texts, config)
        if not unique_missing:
            self.last_ttft_ms = 0
            return [r if r is not None else t for r, t in zip(results, texts)]

        uniq_texts = list(unique_missing.keys())
        sl = _ocr_lang_to_google_sl(config.ocr_language)

        if config.provider == "google_free":
            t0 = time.perf_counter()
            translated = await self._translate_google_free(
                uniq_texts, config.target_language, source_lang=sl
            )
            self.last_ttft_ms = int((time.perf_counter() - t0) * 1000)
            self.last_used_model = "HTTP/2"
            return self._apply_and_cache(
                texts, results, unique_missing, translated, "google_free", config
            )

        # Tiered Routing: send short 1-4 word UI labels to ultra-fast HTTP/2 (~45ms), long lines to LLM
        if config.tiered_routing:
            short_keys = [k for k in uniq_texts if is_short_ui_text(k)]
            long_keys = [k for k in uniq_texts if not is_short_ui_text(k)]
        else:
            short_keys = []
            long_keys = uniq_texts

        # Fast-path when all blocks are short UI labels
        if short_keys and not long_keys:
            t0 = time.perf_counter()
            short_tr = await self._translate_google_free(
                short_keys, config.target_language, source_lang=sl
            )
            self.last_ttft_ms = int((time.perf_counter() - t0) * 1000)
            self.last_used_model = "Tier1-Fast"
            sub_map = {k: unique_missing[k] for k in short_keys}
            return self._apply_and_cache(
                texts, results, sub_map, short_tr, "google_free", config
            )

        # Build incremental merge callback for streaming LLM tokens
        def _handle_llm_partial(mapping: dict[str, str], ttft_ms: int) -> None:
            if on_partial is None or not mapping:
                return
            partial_out = [r if r is not None else t for r, t in zip(results, texts)]
            changed = False
            for idx_str, val in mapping.items():
                if idx_str.isdigit():
                    li = int(idx_str)
                    if 0 <= li < len(long_keys) and val:
                        src_k = long_keys[li]
                        for orig_i in unique_missing[src_k]:
                            partial_out[orig_i] = val
                            changed = True
            if changed:
                on_partial(partial_out, ttft_ms)

        async def _run_short() -> list[str]:
            if not short_keys:
                return []
            tr = await self._translate_google_free(
                short_keys, config.target_language, source_lang=sl
            )
            for src, dst in zip(short_keys, tr):
                final = dst.strip() if dst and dst.strip() else src
                for orig_i in unique_missing[src]:
                    results[orig_i] = final
                if config.use_cache and final != src:
                    self.cache.put("google_free", config.target_language, src, final)
            if on_partial is not None:
                on_partial([r if r is not None else t for r, t in zip(results, texts)], 0)
            return tr

        async def _run_long() -> list[str]:
            if config.provider == "gemini":
                if config.stream_llm and on_partial is not None:
                    return await self._translate_gemini_stream(
                        long_keys, config, on_partial=_handle_llm_partial
                    )
                return await self._translate_gemini(long_keys, config)
            if config.stream_llm and on_partial is not None:
                return await self._translate_openai_stream(
                    long_keys, config, on_partial=_handle_llm_partial
                )
            return await self._translate_openai(long_keys, config)

        if short_keys:
            _, long_tr = await asyncio.gather(_run_short(), _run_long())
        else:
            long_tr = await _run_long()

        long_map = {k: unique_missing[k] for k in long_keys}
        return self._apply_and_cache(
            texts, results, long_map, long_tr, config.provider, config
        )

    async def _translate_google_chunk(
        self,
        client: httpx.AsyncClient,
        chunk: list[str],
        target_lang: str,
        source_lang: str = "auto",
    ) -> list[str]:
        params = [
            ("client", "dict-chrome-ex"),
            ("sl", source_lang),
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
        self, texts: list[str], target_lang: str, source_lang: str = "auto"
    ) -> list[str]:
        client = self._get_google_client()
        chunks = [texts[i : i + 18] for i in range(0, len(texts), 18)]
        try:
            chunk_results = await asyncio.gather(
                *(self._translate_google_chunk(client, c, target_lang, source_lang) for c in chunks)
            )
            return [item for sub in chunk_results for item in sub]
        except Exception:
            async def _one(text: str) -> str:
                resp = await client.get(
                    "https://translate.googleapis.com/translate_a/single",
                    params={
                        "client": "gtx", "sl": source_lang, "tl": target_lang, "dt": "t", "q": text,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                if isinstance(data, list) and data and isinstance(data[0], list):
                    return "".join(part[0] for part in data[0] if part and part[0])
                return text

            return list(await asyncio.gather(*(_one(t) for t in texts)))

    @staticmethod
    def _gemini_payload(texts: list[str], config: AppConfig, model: str) -> tuple[dict, dict]:
        lines_in = "\n".join(f"{i}|{t.replace(chr(10), ' ')}" for i, t in enumerate(texts))
        # Vietnamese diacritics + optional minimal thinking need headroom so output never truncates
        est_tokens = max(384, min(2048, sum(len(t) for t in texts) * 4))
        gen_cfg: dict = {"temperature": 0.1, "maxOutputTokens": est_tokens}
        if "flash-lite" in model.lower():
            gen_cfg["thinkingConfig"] = {"thinkingLevel": "minimal"}
        body = {
            "systemInstruction": {"parts": [{"text": _build_compact_system_prompt(config)}]},
            "contents": [{"role": "user", "parts": [{"text": lines_in}]}],
            "generationConfig": gen_cfg,
        }
        return body, gen_cfg

    @staticmethod
    def _gemini_endpoints(api_key: str, stream: bool) -> list[tuple[str, str]]:
        action = "streamGenerateContent?alt=sse" if stream else "generateContent"
        endpoints = [
            ("AI Studio", f"https://generativelanguage.googleapis.com/v1beta/models/{{model}}:{action}"),
        ]
        if api_key.startswith("AQ."):
            endpoints.append(
                ("Vertex AI Express", f"https://aiplatform.googleapis.com/v1/publishers/google/models/{{model}}:{action}")
            )
        return endpoints

    async def _translate_gemini_stream(
        self,
        texts: list[str],
        config: AppConfig,
        on_partial: Optional[PartialCallback] = None,
    ) -> list[str]:
        api_key = config.gemini_api_key.strip()
        if not api_key:
            raise ValueError("Chưa nhập Gemini / Vertex AI API Key trong Cài đặt.")

        primary_model = config.gemini_model.strip() or "gemini-3.5-flash-lite"
        candidates = [primary_model, *(m for m in GEMINI_FALLBACK_MODELS if m != primary_model)]
        headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}
        endpoints = self._gemini_endpoints(api_key, stream=True)

        client = self._get_llm_client()
        last_err: Optional[str] = None
        for ep_name, ep_tpl in endpoints:
            ep_auth_failed = False
            for m_idx, model in enumerate(candidates):
                url = ep_tpl.format(model=model)
                body, gen_cfg = self._gemini_payload(texts, config, model)
                max_attempts = 2 if m_idx == 0 else 1
                for attempt in range(max_attempts):
                    t0 = time.perf_counter()
                    ttft_ms = 0
                    accum: list[str] = []
                    last_emit_t = 0.0
                    retry_no_thinking = False
                    try:
                        async with client.stream("POST", url, headers=headers, json=body) as resp:
                            if resp.status_code == 400 and "thinkingConfig" in gen_cfg:
                                await resp.aread()
                                gen_cfg.pop("thinkingConfig", None)
                                retry_no_thinking = True
                            elif resp.status_code != 200:
                                raw_err = (await resp.aread()).decode("utf-8", "ignore")
                                last_err = _format_api_error(resp, f"{ep_name} / {model}", raw_err)
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
                            else:
                                async for line in resp.aiter_lines():
                                    if not line.startswith("data: "):
                                        continue
                                    payload = line[6:].strip()
                                    if not payload or payload == "[DONE]":
                                        continue
                                    try:
                                        data = json.loads(payload)
                                    except Exception:
                                        continue
                                    parts = (
                                        data.get("candidates", [{}])[0]
                                        .get("content", {})
                                        .get("parts", [])
                                    )
                                    chunk_txt = "".join(
                                        p.get("text", "") for p in parts if isinstance(p, dict)
                                    )
                                    if not chunk_txt:
                                        continue
                                    if ttft_ms == 0:
                                        ttft_ms = int((time.perf_counter() - t0) * 1000)
                                    accum.append(chunk_txt)
                                    now = time.perf_counter()
                                    if on_partial is not None and (now - last_emit_t) >= 0.045:
                                        partial_map = _parse_pipe_or_json("".join(accum), len(texts))
                                        if partial_map:
                                            on_partial(partial_map, ttft_ms)
                                            last_emit_t = now
                    except httpx.TimeoutException:
                        last_err = f"Timeout ({ep_name} / {model})"
                        continue

                    if retry_no_thinking:
                        # Retry once immediately without thinkingConfig
                        try:
                            async with client.stream("POST", url, headers=headers, json=body) as resp2:
                                if resp2.status_code == 200:
                                    async for line in resp2.aiter_lines():
                                        if not line.startswith("data: "):
                                            continue
                                        payload = line[6:].strip()
                                        if not payload or payload == "[DONE]":
                                            continue
                                        try:
                                            data = json.loads(payload)
                                        except Exception:
                                            continue
                                        parts = (
                                            data.get("candidates", [{}])[0]
                                            .get("content", {})
                                            .get("parts", [])
                                        )
                                        chunk_txt = "".join(
                                            p.get("text", "") for p in parts if isinstance(p, dict)
                                        )
                                        if chunk_txt:
                                            if ttft_ms == 0:
                                                ttft_ms = int((time.perf_counter() - t0) * 1000)
                                            accum.append(chunk_txt)
                                            if on_partial is not None:
                                                pm = _parse_pipe_or_json("".join(accum), len(texts))
                                                if pm:
                                                    on_partial(pm, ttft_ms)
                                else:
                                    raw_err = (await resp2.aread()).decode("utf-8", "ignore")
                                    last_err = _format_api_error(resp2, f"{ep_name} / {model}", raw_err)
                                    break
                        except httpx.TimeoutException:
                            last_err = f"Timeout ({ep_name} / {model})"
                            continue

                    if accum:
                        raw_text = "".join(accum)
                        mapping = _parse_pipe_or_json(raw_text, len(texts))
                        self.last_used_model = model
                        self.last_ttft_ms = ttft_ms or int((time.perf_counter() - t0) * 1000)
                        return [mapping.get(str(i), t) for i, t in enumerate(texts)]
                if ep_auth_failed:
                    break
            if not ep_auth_failed:
                break

        raise RuntimeError(last_err or "Không thể kết nối Gemini API.")

    async def _translate_gemini(
        self, texts: list[str], config: AppConfig
    ) -> list[str]:
        api_key = config.gemini_api_key.strip()
        if not api_key:
            raise ValueError("Chưa nhập Gemini / Vertex AI API Key trong Cài đặt.")

        primary_model = config.gemini_model.strip() or "gemini-3.5-flash-lite"
        candidates = [primary_model, *(m for m in GEMINI_FALLBACK_MODELS if m != primary_model)]
        headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}
        endpoints = self._gemini_endpoints(api_key, stream=False)

        client = self._get_llm_client()
        last_err: Optional[str] = None
        for ep_name, ep_tpl in endpoints:
            ep_auth_failed = False
            for m_idx, model in enumerate(candidates):
                url = ep_tpl.format(model=model)
                body, gen_cfg = self._gemini_payload(texts, config, model)
                max_attempts = 2 if m_idx == 0 else 1
                for attempt in range(max_attempts):
                    t0 = time.perf_counter()
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
                        self.last_ttft_ms = int((time.perf_counter() - t0) * 1000)
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

    @staticmethod
    def _openai_payload(
        texts: list[str], config: AppConfig, base_url: str, model: str, stream: bool
    ) -> dict:
        lines_in = "\n".join(f"{i}|{t.replace(chr(10), ' ')}" for i, t in enumerate(texts))
        is_qwen3 = "qwen-3" in model.lower() or "qwen3" in model.lower()
        system_msg = _build_compact_system_prompt(config, is_qwen3=is_qwen3)
        est_tokens = max(256, min(1536, sum(len(t) for t in texts) * 3))
        payload: dict = {
            "model": model,
            "temperature": 0.1,
            "max_tokens": est_tokens,
            "stream": stream,
            "messages": [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": lines_in},
            ],
        }
        # Local Ollama / llama.cpp: keep KV-cache hot in memory
        if "localhost" in base_url or "127.0.0.1" in base_url:
            payload["keep_alive"] = "15m"
        return payload

    async def _translate_openai_stream(
        self,
        texts: list[str],
        config: AppConfig,
        on_partial: Optional[PartialCallback] = None,
    ) -> list[str]:
        api_key = config.openai_api_key.strip()
        base_url = resolve_openai_base_url(config)
        model = config.openai_model.strip() or "gpt-4o-mini"

        if not api_key and "localhost" not in base_url and "127.0.0.1" not in base_url:
            raise ValueError("Chưa nhập OpenAI / Groq / Cerebras API Key trong Cài đặt.")

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload = self._openai_payload(texts, config, base_url, model, stream=True)
        t0 = time.perf_counter()
        ttft_ms = 0
        accum: list[str] = []
        last_emit_t = 0.0

        async with self._get_llm_client().stream(
            "POST", f"{base_url}/chat/completions", headers=headers, json=payload
        ) as resp:
            if resp.status_code != 200:
                raw_err = (await resp.aread()).decode("utf-8", "ignore")
                raise RuntimeError(_format_api_error(resp, model, raw_err))
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:].strip()
                if not data_str or data_str == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                except Exception:
                    continue
                delta = (
                    chunk.get("choices", [{}])[0].get("delta", {}).get("content") or ""
                )
                if not delta:
                    continue
                accum.append(delta)
                cleaned = _strip_think_tags("".join(accum))
                if not cleaned.strip():
                    continue
                if ttft_ms == 0:
                    ttft_ms = int((time.perf_counter() - t0) * 1000)
                now = time.perf_counter()
                if on_partial is not None and (now - last_emit_t) >= 0.045:
                    partial_map = _parse_pipe_or_json(cleaned, len(texts))
                    if partial_map:
                        on_partial(partial_map, ttft_ms)
                        last_emit_t = now

        raw_text = "".join(accum)
        mapping = _parse_pipe_or_json(raw_text, len(texts))
        self.last_used_model = model
        self.last_ttft_ms = ttft_ms or int((time.perf_counter() - t0) * 1000)
        return [mapping.get(str(i), t) for i, t in enumerate(texts)]

    async def _translate_openai(
        self, texts: list[str], config: AppConfig
    ) -> list[str]:
        api_key = config.openai_api_key.strip()
        base_url = resolve_openai_base_url(config)
        model = config.openai_model.strip() or "gpt-4o-mini"

        if not api_key and "localhost" not in base_url and "127.0.0.1" not in base_url:
            raise ValueError("Chưa nhập OpenAI / Groq / Cerebras API Key trong Cài đặt.")

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload = self._openai_payload(texts, config, base_url, model, stream=False)
        t0 = time.perf_counter()
        resp = await self._get_llm_client().post(
            f"{base_url}/chat/completions", headers=headers, json=payload
        )
        if resp.status_code != 200:
            raise RuntimeError(_format_api_error(resp, model))

        raw_text = (
            resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
        )
        mapping = _parse_pipe_or_json(raw_text, len(texts))
        self.last_used_model = model
        self.last_ttft_ms = int((time.perf_counter() - t0) * 1000)
        return [mapping.get(str(i), t) for i, t in enumerate(texts)]
