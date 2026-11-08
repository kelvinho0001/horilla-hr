"""
Direct ZKTeco device connector using the ``pyzk`` library Horilla already
depends on. Read-only: it never clears device logs. Whether a given model
(e.g. uFace 202) answers this protocol must be verified on site.
"""

from .base import (
    FETCH_EMPLOYEES,
    FETCH_TRANSACTIONS,
    TEST_CONNECTION,
    BaseConnector,
    ConnectorError,
    ExternalEmployee,
    NormalizedTransaction,
)


class DirectZKTecoConnector(BaseConnector):
    provider_type = "direct_zkteco"
    capabilities = frozenset({TEST_CONNECTION, FETCH_TRANSACTIONS, FETCH_EMPLOYEES})

    def _zk(self):
        device = self.provider.device
        if device is None or not device.machine_ip or not device.port:
            raise ConnectorError("No Horilla device with an IP and port is linked.")
        try:
            from zk import ZK
        except ImportError as exc:  # pragma: no cover
            raise ConnectorError("pyzk is not installed") from exc
        try:
            password = int(device.zk_password or 0)
        except ValueError as exc:
            raise ConnectorError("ZK device password must be numeric") from exc
        return ZK(device.machine_ip, port=device.port, timeout=30, password=password)

    def _connect(self):
        try:
            return self._zk().connect()
        except ConnectorError:
            raise
        except Exception as exc:
            raise ConnectorError(f"Cannot connect to device: {exc}") from exc

    def test_connection(self):
        try:
            conn = self._connect()
        except ConnectorError as exc:
            return False, str(exc)
        try:
            return True, f"Connected. Firmware {conn.get_firmware_version()}"
        finally:
            conn.disconnect()

    def fetch_transactions(self, since=None, until=None):
        conn = self._connect()
        try:
            logs = conn.get_attendance() or []
        except Exception as exc:
            raise ConnectorError(f"Reading attendance failed: {exc}") from exc
        finally:
            conn.disconnect()
        device_id = str(getattr(self.provider.device, "pk", ""))
        txns = []
        for log in logs:
            stamp = log.timestamp
            if since and stamp < since or until and stamp > until:
                continue
            txns.append(
                NormalizedTransaction(
                    external_employee_id=str(log.user_id).strip(),
                    timestamp=stamp,
                    external_device_id=device_id,
                    verification_method=str(getattr(log, "status", "")),
                    raw={"uid": getattr(log, "uid", None), "punch": getattr(log, "punch", None)},
                )
            )
        return txns, []

    def fetch_employees(self):
        conn = self._connect()
        try:
            users = conn.get_users() or []
        except Exception as exc:
            raise ConnectorError(f"Reading users failed: {exc}") from exc
        finally:
            conn.disconnect()
        return [
            ExternalEmployee(
                external_employee_id=str(u.user_id).strip(),
                name=getattr(u, "name", "") or "",
                raw={"uid": getattr(u, "uid", None)},
            )
            for u in users
        ]
