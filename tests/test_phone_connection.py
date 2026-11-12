"""Behaviour: which phone the adb client picks over USB and the Wi-Fi fallback."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from paperscan.capture import adb_import
from paperscan.capture.adb_import import AdbClient, AdbError, _parse_mdns_line

ADB = "adb"
USB_LINE = "R58M123ABC device product:x model:Pixel_8 device:y"
WIFI_SERIAL = "192.168.1.20:41237"
WIFI_LINE = f"{WIFI_SERIAL} device product:x model:Pixel_8 device:y"
HEADER = "List of devices attached"
MDNS_OUTPUT = (
    "List of discovered mdns services\n"
    "adb-R58M123ABC-AbCdEf\t_adb-tls-pairing._tcp\t192.168.1.20:37011\n"
    f"adb-R58M123ABC-AbCdEf\t_adb-tls-connect._tcp\t{WIFI_SERIAL}\n"
)

#: Maps the adb arguments (without the executable) to stdout; records every call.
FakeAdb = Callable[[dict[str, str]], list[list[str]]]


@pytest.fixture
def fake_adb(monkeypatch: pytest.MonkeyPatch) -> FakeAdb:
    """Replace the adb executable with canned replies keyed by the joined arguments."""

    def install(replies: dict[str, str]) -> list[list[str]]:
        calls: list[list[str]] = []

        def run_tool(arguments: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
            del timeout
            calls.append(arguments[1:])
            stdout = replies.get(" ".join(arguments[1:]), "")
            return subprocess.CompletedProcess(arguments, 0, stdout=stdout, stderr="")

        monkeypatch.setattr(adb_import, "require_tool", lambda _tool, _configured="": Path(ADB))
        monkeypatch.setattr(adb_import, "run_tool", run_tool)
        return calls

    return install


def test_usb_phone_is_preferred_over_a_wireless_one(fake_adb: FakeAdb) -> None:
    fake_adb({"devices -l": f"{HEADER}\n{WIFI_LINE}\n{USB_LINE}\n"})
    device = AdbClient(wireless_fallback=True).connect()
    assert device.serial == "R58M123ABC"
    assert device.label == "Pixel 8 (USB)"


def test_without_fallback_a_missing_usb_phone_is_reported(fake_adb: FakeAdb) -> None:
    calls = fake_adb({"devices -l": f"{HEADER}\n"})
    with pytest.raises(AdbError, match="No phone found"):
        AdbClient().connect()
    assert ["mdns", "services"] not in calls


def test_fallback_connects_to_the_phone_announced_over_mdns(fake_adb: FakeAdb) -> None:
    calls = fake_adb(
        {
            "devices -l": f"{HEADER}\n",
            "mdns services": MDNS_OUTPUT,
            f"connect {WIFI_SERIAL}": f"connected to {WIFI_SERIAL}",
        }
    )
    client = AdbClient(wireless_fallback=True)
    device = client.connect()
    assert device.serial == WIFI_SERIAL
    assert device.wireless
    client.list_photos()
    assert calls[-1][:2] == ["-s", WIFI_SERIAL]


def test_configured_address_gets_the_classic_port(fake_adb: FakeAdb) -> None:
    calls = fake_adb({"devices -l": f"{HEADER}\n", "connect 192.168.1.20:5555": "already connected to 192.168.1.20:5555"})
    device = AdbClient(wireless_fallback=True, wireless_address="192.168.1.20").connect()
    assert device.serial == "192.168.1.20:5555"
    assert ["mdns", "services"] not in calls


def test_failed_wifi_connect_explains_both_ways(fake_adb: FakeAdb) -> None:
    fake_adb({"devices -l": f"{HEADER}\n", f"connect {WIFI_SERIAL}": f"failed to connect to {WIFI_SERIAL}"})
    with pytest.raises(AdbError, match=r"USB or Wi-Fi \(failed to connect"):
        AdbClient(wireless_fallback=True, wireless_address=WIFI_SERIAL).connect()


def test_several_usb_phones_need_a_serial(fake_adb: FakeAdb) -> None:
    fake_adb({"devices -l": f"{HEADER}\n{USB_LINE}\nX99 device model:Other\n"})
    with pytest.raises(AdbError, match="Several phones"):
        AdbClient().connect()


def test_pairing_checks_the_reply(fake_adb: FakeAdb) -> None:
    fake_adb({"pair 192.168.1.20:37011 482913": "Successfully paired to 192.168.1.20:37011 [guid=adb-x]"})
    AdbClient().pair("192.168.1.20:37011", "482913")
    with pytest.raises(AdbError):
        AdbClient().pair("192.168.1.20:37011", "000000")


def test_mdns_parsing_skips_pairing_services() -> None:
    lines = MDNS_OUTPUT.splitlines()
    assert [_parse_mdns_line(line) for line in lines] == [None, None, WIFI_SERIAL]
