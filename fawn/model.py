from dataclasses import dataclass, field
from typing import List


@dataclass
class Item:
    id: str
    url: str
    platform: str
    status: str
    created_at: str
    updated_at: str
    title: str = ""
    author: str = ""
    user_comment: str = ""
    summary: str = ""
    tags: List[str] = field(default_factory=list)
    categories: List[str] = field(default_factory=list)
    media_type: str = "link"
    assets: List[str] = field(default_factory=list)
    transcript: str = ""
