from types import SimpleNamespace

from vinted_monitor.core.config import Settings
from vinted_monitor.providers.datadome import DataDomeChallengeError
from vinted_monitor.services.runs import SUCCESS
from vinted_monitor.services.task_queue import MonitorTask
from vinted_monitor.worker.consumer import TaskConsumer


def test_consumer_retries_datadome_challenge_with_new_proxy_session(monkeypatch) -> None:
    profile = SimpleNamespace(name="chrome_test")
    proxy_urls: list[str] = []
    session_ids: list[str] = []

    class FakeProvider:
        def __init__(self, *args, proxy_url=None, **kwargs) -> None:
            self.proxy_url = proxy_url
            proxy_urls.append(proxy_url)

        def close(self) -> None:
            return None

    attempts = 0

    def fake_execute(self, task, provider, profile, session_id, attempt, proxy_url):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise DataDomeChallengeError("challenge")
        return SimpleNamespace(
            id=42,
            status=SUCCESS,
            items_found=0,
            items_new=0,
        )

    def fake_proxy_url(self, task, session_id):
        session_ids.append(session_id)
        return f"http://proxy.example/{session_id}"

    monkeypatch.setattr("vinted_monitor.worker.consumer.profile_for_impersonate", lambda _impersonate: profile)
    monkeypatch.setattr("vinted_monitor.worker.consumer.CurlCffiVintedCatalogProvider", FakeProvider)
    monkeypatch.setattr(TaskConsumer, "_execute_run", fake_execute)
    monkeypatch.setattr(TaskConsumer, "_proxy_url_for_attempt", fake_proxy_url)

    consumer = TaskConsumer(Settings(worker_max_retry_attempts=2), consumer_id=0)
    task = MonitorTask(
        source_id=1,
        source_url="https://www.vinted.es/catalog?search_text=nike",
        monitor_mode="window",
        trigger="scheduler",
    )

    consumer._process_with_escalation(task)

    assert attempts == 2
    assert len(proxy_urls) == 2
    assert proxy_urls[0] != proxy_urls[1]
    assert session_ids[0] != session_ids[1]
