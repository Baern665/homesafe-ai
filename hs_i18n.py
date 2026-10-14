"""Local-only UI translation; user documents are never translated or transmitted."""
from __future__ import annotations
import json
import re
from functools import lru_cache
from pathlib import Path

LANGUAGES = ["한국어", "English", "日本語", "中文", "Español"]

@lru_cache(maxsize=1)
def catalog():
    return json.loads(Path(__file__).with_name("ui_translations.json").read_text(encoding="utf-8"))

def translate(text, language="한국어", **values):
    if not isinstance(text, str):
        return text
    result = text if language == "한국어" else catalog().get(language, {}).get(text, text)
    return result.format(**values) if values else result

def translate_error(text, language):
    if language == "한국어":
        return text
    table = catalog().get(language, {})
    if text in table:
        return table[text]
    for source, target in table.items():
        if "{" not in source or "}" not in source:
            continue
        tokens = re.findall(r"\{[^{}]+\}", source)
        pattern = re.escape(source)
        for token in tokens:
            pattern = pattern.replace(re.escape(token), "(.+?)", 1)
        match = re.fullmatch(pattern, text, flags=re.DOTALL)
        if match:
            for token, value in zip(tokens, match.groups()):
                target = target.replace(token, value, 1)
            return target
    code = re.search(r"(?:[A-Z]{2,}(?:-[A-Z]{2,})+)", text)
    generic = translate("요청을 처리하지 못했습니다. 입력 형식과 이용 한도를 확인하고 다시 시도해 주세요.", language)
    return generic + (" (" + code.group(0) + ")" if code else "")

class LocalizedUI:
    """Translate labels, not values, state keys, URLs or contract quotations."""
    TEXT_METHODS = {"title", "header", "subheader", "caption", "markdown", "write", "info", "success", "button", "checkbox", "text_area", "text_input", "expander", "spinner", "form_submit_button", "download_button", "file_uploader", "link_button", "selectbox", "radio"}
    def __init__(self, target, language):
        self.target, self.language = target, language
    def __enter__(self):
        self.target.__enter__()
        return self
    def __exit__(self, *args):
        return self.target.__exit__(*args)
    def __getattr__(self, name):
        method = getattr(self.target, name)
        if name == "columns":
            return lambda *args, **kwargs: [LocalizedUI(col, self.language) for col in method(*args, **kwargs)]
        if name == "tabs":
            return lambda labels, **kwargs: method([translate(label, self.language) for label in labels], **kwargs)
        if name not in self.TEXT_METHODS | {"error", "warning"}:
            return method
        def render(*args, **kwargs):
            args = list(args)
            if args:
                args[0] = translate_error(args[0], self.language) if name in {"error", "warning"} else translate(args[0], self.language)
            for key in ("placeholder", "help"):
                if key in kwargs:
                    kwargs[key] = translate(kwargs[key], self.language)
            if name == "radio" and "format_func" not in kwargs:
                kwargs["format_func"] = lambda value: translate(value, self.language)
            return method(*args, **kwargs)
        return render
