"""NLLB-обёртка: загрузка модели, определение языка по письменности, батчи.

Модель — nllb-200-distilled-600M (PLAN.md, фаза 5): компромисс качество/память
(~2.4 ГБ RSS в fp32, быстрее в fp16/CTranslate2 при необходимости); полный
1.3B для MVP не нужен. Поверх Redis-кэша бэкенда — свой in-process LRU-кэш:
повторные заголовки («현대 그랜저», «奔驰 E级») не должны доходить до модели.
"""

import gc
import logging
import os
import re
import threading

logger = logging.getLogger(__name__)

# Письменность → NLLB-код. Определяем только по скрипту, без langdetect:
# корейские тайтлы иногда содержат латиницу, детект по большинству символов
# скрипта устойчивее.
_HANGUL = re.compile("[\u1100-\u11ff\u3130-\u318f\uac00-\ud7af]")
_CJK = re.compile("[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_CYRILLIC = re.compile("[\u0400-\u04ff]")

_CACHE_MAX = 50_000
# Макс. текстов за один прогон модели: постоянные большие батчи — главный
# источник роста KV-кэша/RSS у NLLB на CPU (фидбек «утечки памяти»).
_CHUNK = int(os.getenv("TRANSLATE_CHUNK", "16"))


def detect_lang(text: str) -> str:
    """NLLB-код языка по письменности (для source_lang='auto')."""
    if _HANGUL.search(text):
        return "kor_Hang"
    if _CJK.search(text):
        return "zho_Hans"
    if _CYRILLIC.search(text):
        return "rus_Cyrl"
    return "eng_Latn"


class NLLBTranslator:
    """Ленивая загрузка пайплайна transformers + потокобезопасный кэш."""

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        self._pipe = None
        self._cache: dict[tuple[str, str], str] = {}
        self._lock = threading.Lock()
        # tokenizers (Rust) не потокобезопасны: параллельные вызовы pipeline
        # из нескольких request-потоков падают «RuntimeError: Already borrowed».
        # Инференс сериализуем, кэш-поиск может идти параллельно.
        self._infer_lock = threading.Lock()

    @property
    def loaded(self) -> bool:
        return self._pipe is not None

    def load(self) -> None:
        """Загрузка модели (тяжёлый импорт transformers — лениво).

        float16: весов вдвое меньше (~1.2ГБ vs ~2.4ГБ fp32) — на хосте с 6ГБ RAM
        иначе не сходится бюджет с qdrant/rag; инференс на CPU всё равно
        выполняется во float32, качество то же.
        """
        if self._pipe is not None:
            return
        import torch  # noqa: PLC0415
        from transformers import pipeline  # noqa: PLC0415

        logger.info("loading_nllb: %s (fp16)", self.model_name)
        self._pipe = pipeline(
            "translation",
            model=self.model_name,
            torch_dtype=torch.float16,  # CPU по умолчанию
        )
        logger.info("nllb_loaded")

    def warmup(self) -> None:
        """Прогрев при старте: иначе первый батч после рестарта уйдёт с fallback."""
        self.load()
        self.translate_batch(["점검 중"], source_lang="kor_Hang")

    def translate_batch(
        self,
        texts: list[str],
        source_lang: str = "auto",
        target_lang: str = "rus_Cyrl",
    ) -> list[str | None]:
        """Перевести список тайтлов; неизвестный язык/пустая строка → None."""
        self.load()
        assert self._pipe is not None  # после load()
        results: list[str | None] = [None] * len(texts)

        groups: dict[str, list[tuple[int, str]]] = {}
        with self._lock:
            for i, text in enumerate(texts):
                stripped = text.strip()
                if not stripped:
                    continue
                lang = source_lang if source_lang != "auto" else detect_lang(stripped)
                key = (lang, stripped)
                hit = self._cache.get(key)
                if hit is not None:
                    results[i] = hit
                else:
                    groups.setdefault(lang, []).append((i, stripped))

        for lang, items in groups.items():
            # Чанками: 64 текста за прогон держат в памяти большой KV-кэш,
            # который transformers на CPU не всегда возвращает системе.
            with self._infer_lock:  # tokenizers: «Already borrowed» без лока
                for start in range(0, len(items), _CHUNK):
                    chunk = items[start : start + _CHUNK]
                    outputs = self._pipe(
                        [t for _, t in chunk],
                        src_lang=lang,
                        tgt_lang=target_lang,
                        max_new_tokens=128,
                    )
                    for (i, stripped), out in zip(chunk, outputs):
                        translated = (out.get("translation_text") or "").strip() or None
                        results[i] = translated
                        if translated:
                            with self._lock:
                                if len(self._cache) >= _CACHE_MAX:
                                    self._cache.clear()  # простой reset вместо LRU-вытеснения
                                self._cache[(lang, stripped)] = translated
                    gc.collect()  # вернуть активационную память после каждого чанка
        return results