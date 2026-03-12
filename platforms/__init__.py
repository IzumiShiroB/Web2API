from .base import BasePlatform
from .deepseek import DeepSeekPlatform
from .doubao import DoubaoPlatform

__all__ = ["BasePlatform", "DeepSeekPlatform", "DoubaoPlatform", "get_platform"]

PLATFORMS = {
    "deepseek": DeepSeekPlatform,
    "doubao": DoubaoPlatform,
}


def get_platform(name: str, data_dir: str = "browser_data") -> BasePlatform:
    if name.lower() not in PLATFORMS:
        raise ValueError(f"Unknown platform: {name}. Available: {list(PLATFORMS.keys())}")
    return PLATFORMS[name.lower()](data_dir)
