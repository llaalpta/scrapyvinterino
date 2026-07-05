from __future__ import annotations

import random
import uuid

import structlog

from vinted_monitor.core.config import Settings, get_settings
from vinted_monitor.db.session import SessionLocal
from vinted_monitor.providers.browser_profiles import select_random_profile
from vinted_monitor.providers.datadome import DataDomeChallengeError
from vinted_monitor.providers.vinted_catalog import CurlCffiVintedCatalogProvider
from vinted_monitor.services.proxies import mark_proxy_challenge_detected, mark_proxy_run_failure, mark_proxy_run_success
from vinted_monitor.services.runs import SCHEDULER_TRIGGER, execute_monitor_run
from vinted_monitor.services.scheduler import RunEgress
from vinted_monitor.services.seen_cache import get_seen_cache
from vinted_monitor.services.task_queue import MonitorTask, TaskQueueError, dequeue_task


class TaskConsumer:
    """Consumer worker: dequeues tasks from Redis and processes them with anti-bot evasion.

    Each consumed task goes through the full evasion lifecycle:
    1. Select a random browser profile (coherent impersonate + UA + headers)
    2. Generate a unique UUID for proxy sticky session
    3. Create a curl_cffi provider with the profile and proxy
    4. Execute the monitor run (bootstrap → delay → catalog → dedup → filters → opportunities)
    5. Discard the session, proxy, and cookies

    On DataDome challenge detection, retries with escalation: new IP, new profile, longer delay.
    """

    def __init__(self, settings: Settings, consumer_id: int = 0) -> None:
        self.settings = settings
        self.consumer_id = consumer_id
        self.rng = random.Random()
        self.logger = structlog.get_logger().bind(consumer_id=consumer_id)

    def run_forever(self) -> None:
        """Block on BRPOP and process tasks indefinitely."""
        self.logger.info("consumer_started", queue_key=self.settings.worker_task_queue_key)
        cache = get_seen_cache()
        while True:
            try:
                task = dequeue_task(
                    cache.client,
                    timeout=self.settings.worker_blpop_timeout_seconds,
                    queue_key=self.settings.worker_task_queue_key,
                )
            except TaskQueueError as exc:
                self.logger.error("consumer_dequeue_error", error=str(exc))
                continue
            except Exception as exc:
                self.logger.error("consumer_dequeue_unexpected_error", error=str(exc))
                continue

            if task is None:
                # Timeout — no task available, loop back
                continue

            self.logger.info(
                "consumer_task_received",
                source_id=task.source_id,
                task_id=task.task_id,
                trigger=task.trigger,
            )
            try:
                self._process_with_escalation(task)
            except Exception as exc:
                self.logger.error(
                    "consumer_task_failed",
                    source_id=task.source_id,
                    task_id=task.task_id,
                    error=str(exc),
                )

    def _process_with_escalation(self, task: MonitorTask) -> None:
        """Process a task with retry escalation on DataDome challenges."""
        max_attempts = self.settings.worker_max_retry_attempts

        for attempt in range(1, max_attempts + 1):
            profile = select_random_profile(self.rng)
            session_id = str(uuid.uuid4())

            self.logger.info(
                "consumer_attempt_start",
                source_id=task.source_id,
                task_id=task.task_id,
                attempt=attempt,
                browser_profile=profile.name,
                session_id=session_id[:8],  # Safe prefix for logging
            )

            provider = CurlCffiVintedCatalogProvider(
                settings=get_settings(),
                profile=profile,
                proxy_url=task.proxy_url_template,
                timeout_ms=self.settings.vinted_request_timeout_ms,
                catalog_per_page=self.settings.vinted_fast_catalog_per_page,
                request_retries=0,  # We handle retries at consumer level
                human_delay_min=self.settings.human_delay_min_seconds,
                human_delay_max=self.settings.human_delay_max_seconds,
            )

            try:
                self._execute_run(task, provider, profile, session_id, attempt)
                # Success — mark proxy healthy
                if task.proxy_profile_id:
                    with SessionLocal() as db:
                        mark_proxy_run_success(db, task.proxy_profile_id)
                        db.commit()
                return

            except DataDomeChallengeError:
                self.logger.warning(
                    "consumer_datadome_challenge",
                    source_id=task.source_id,
                    task_id=task.task_id,
                    attempt=attempt,
                    browser_profile=profile.name,
                )
                # Penalize proxy with DataDome-specific multiplier
                if task.proxy_profile_id:
                    with SessionLocal() as db:
                        mark_proxy_challenge_detected(
                            db,
                            task.proxy_profile_id,
                            penalty_multiplier=self.settings.datadome_challenge_penalty_multiplier,
                        )
                        db.commit()
            except Exception as exc:
                self.logger.error(
                    "consumer_attempt_error",
                    source_id=task.source_id,
                    task_id=task.task_id,
                    attempt=attempt,
                    error=str(exc),
                )
                if task.proxy_profile_id:
                    with SessionLocal() as db:
                        mark_proxy_run_failure(db, task.proxy_profile_id)
                        db.commit()
            finally:
                provider.close()

        # All attempts exhausted
        self.logger.error(
            "consumer_all_attempts_exhausted",
            source_id=task.source_id,
            task_id=task.task_id,
            max_attempts=max_attempts,
        )

    def _execute_run(
        self,
        task: MonitorTask,
        provider: CurlCffiVintedCatalogProvider,
        profile: object,
        session_id: str,
        attempt: int,
    ) -> None:
        """Execute the monitor run using the pre-configured provider."""
        egress = RunEgress(
            mode="proxy" if task.proxy_profile_id else "direct",
            proxy_profile_id=task.proxy_profile_id,
            proxy_url=task.proxy_url_template,
        )

        trigger = task.trigger or SCHEDULER_TRIGGER

        with SessionLocal() as db:
            run = execute_monitor_run(
                db,
                task.source_id,
                provider=provider,
                trigger=trigger,
                egress=egress,
            )
            self.logger.info(
                "consumer_run_finished",
                source_id=task.source_id,
                task_id=task.task_id,
                run_id=run.id,
                status=run.status,
                items_found=run.items_found,
                items_new=run.items_new,
                attempt=attempt,
            )
