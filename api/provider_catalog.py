from __future__ import annotations

TIKU_PROVIDER_LABELS = {
    "TikuYanxi": "言溪题库",
    "SiliconFlow": "硅基流动",
    "AI": "AI 大模型",
    "TikuLike": "LIKE 知识库",
    "TikuAdapter": "TikuAdapter",
    "LotusTiKu": "荷花题库",
    "MultiTiku": "多题库协同",
}

PROVIDER_OPTIONS = ["TikuYanxi", "SiliconFlow", "AI", "TikuLike", "TikuAdapter", "LotusTiKu", "MultiTiku"]
COLLAB_PROVIDER_OPTIONS = ["TikuYanxi", "SiliconFlow", "AI", "TikuLike", "TikuAdapter", "LotusTiKu"]
DECISION_PROVIDER_OPTIONS = ["SiliconFlow", "AI", "TikuYanxi", "TikuLike", "TikuAdapter", "LotusTiKu"]

PROVIDER_VALUE_BY_LABEL = {label: value for value, label in TIKU_PROVIDER_LABELS.items()}


def provider_label(provider_value: str) -> str:
    return TIKU_PROVIDER_LABELS.get(str(provider_value).strip(), str(provider_value).strip() or "未配置")


def provider_from_label(label: str, default: str = "TikuYanxi") -> str:
    return PROVIDER_VALUE_BY_LABEL.get(str(label).strip(), default)


def provider_items(values: list[str] | None = None) -> list[tuple[str, str]]:
    sequence = values or PROVIDER_OPTIONS
    return [(value, provider_label(value)) for value in sequence]
