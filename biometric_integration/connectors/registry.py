"""Maps ``ProviderConfig.provider_type`` to a connector class. Add new vendors here."""

from .base import NotSupported
from .direct_zkteco import DirectZKTecoConnector
from .manual_file import ManualFileConnector

CONNECTORS = {
    ManualFileConnector.provider_type: ManualFileConnector,
    DirectZKTecoConnector.provider_type: DirectZKTecoConnector,
    # biotime / timenet are registered once discovery on the installed
    # versions is complete.
}


def get_connector(provider):
    try:
        return CONNECTORS[provider.provider_type](provider)
    except KeyError as exc:
        raise NotSupported(
            f"No connector is available yet for '{provider.provider_type}'."
        ) from exc
