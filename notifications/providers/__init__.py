"""Push provider registry."""

from .apns import APNsProvider
from .base import ErrorClassification, ProviderResult, PushMessage, PushProvider, build_push_payload
from .config import APNsConfig, FCMConfig
from .fcm import FCMProvider


def providers_from_environment() -> dict[str, PushProvider]:
    fcm = FCMConfig.from_env()
    apns = APNsConfig.from_env()
    providers: dict[str, PushProvider] = {}
    if fcm.enabled:
        providers["fcm"] = FCMProvider(fcm)
    if apns.enabled:
        providers["apns"] = APNsProvider(apns)
    return providers


__all__ = [
    "APNsConfig", "APNsProvider", "ErrorClassification", "FCMConfig", "FCMProvider",
    "ProviderResult", "PushMessage", "PushProvider", "build_push_payload",
    "providers_from_environment",
]
