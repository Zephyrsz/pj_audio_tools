"""后端语言参数适配，避免切换 ASR 后还要修改整条 pipeline 的语言。"""

NAMES = {
    "en": "English", "ja": "Japanese", "zh": "Chinese", "ko": "Korean",
    "de": "German", "fr": "French", "es": "Spanish", "it": "Italian",
    "pt": "Portuguese", "ru": "Russian", "hi": "Hindi", "ar": "Arabic",
    "yue": "Cantonese", "nl": "Dutch", "tr": "Turkish", "vi": "Vietnamese",
    "th": "Thai", "id": "Indonesian",
}


def language_name(value):
    return NAMES.get(value.lower(), value) if value else value


def language_code(value):
    if not value:
        return value
    return next((code for code, name in NAMES.items() if name.lower() == value.lower()), value)
