import pytest

from yowayowa.domain import LicenseClass
from yowayowa.providers.base import ProviderDescriptor, ProviderPolicyError, enforce_provider_policy


def test_personal_provider_allowed_in_personal_mode() -> None:
    descriptor = ProviderDescriptor("demo", LicenseClass.PERSONAL_ONLY, False, "test")
    enforce_provider_policy(descriptor, mode="personal")


def test_personal_provider_blocked_in_public_mode() -> None:
    descriptor = ProviderDescriptor("demo", LicenseClass.PERSONAL_ONLY, False, "test")
    with pytest.raises(ProviderPolicyError):
        enforce_provider_policy(descriptor, mode="public")
