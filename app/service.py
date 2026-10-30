"""PROPOSED: HTTP-facing facade over the durable coordinator execution lane."""
from pathlib import Path
import uuid

from . import policy
from .coordinator import Coordinator
from .execution import (Error, CONFIG_SHA, classification_context, config_sha,
                        exact, identifier, failure, validate_request)


class Service:
    def __init__(self, database=None, workspace_base=None, task_timeout=180,
                 worker_command=None, autostart=True, clock=None, legacy_quiescent=False, live_config=None, bounded_live_runtime=False):
        # Preserve the old constructor's path meaning; Coordinator owns a sibling DB.
        self.database = Path(database or Path(__file__).resolve().parents[1] / 'artifacts' / 'journey.sqlite')
        self.coordinator = Coordinator(self.database, workspace_base=workspace_base,
                                       task_timeout=task_timeout, worker_command=worker_command,
                                       autostart=autostart, clock=clock,
                                       legacy_quiescent=legacy_quiescent, live_config=live_config, bounded_live_runtime=bounded_live_runtime)

    def mutate(self, action, run, body, key):
        identifier(key)
        if run is not None:
            identifier(run)
        acknowledgement = self.coordinator.admit(action, run, body, key)
        if action in ('create', 'create-preserving'):
            # Admission has committed before this returns; no factory wait here.
            return 202, acknowledgement
        return self.coordinator.wait(acknowledgement['request_id'])

    def status(self, run, after=0):
        identifier(run)
        return self.coordinator.status(run, after)

    def scan(self, run):
        identifier(run)
        acknowledgement = self.coordinator.admit('scan', run, {}, str(uuid.uuid4()))
        status, result = self.coordinator.wait(acknowledgement['request_id'])
        if not 200 <= status < 300:
            raise Error(result.get('error', {}).get('code', 'SCAN_FAILED'), status)
        return result

    def bundle(self, run):
        identifier(run)
        return self.coordinator.stored_export(run)

    def export(self, run):
        return self.bundle(run)

    def session_view(self):
        return {**self.coordinator.session_view(), 'public_intent_policy': policy.PUBLIC_INTENT_POLICY,
                'coordinator_policy': {
                    'worker_limit': 1, 'waiting_run_limit': 10,
                    'machine_task_seconds': self.coordinator.task_timeout,
                    'queue_time_counts': True,
                    'approval_wait_states': ['AWAITING_APPROVAL', 'APPROVED'],
                    'status': 'Stored observation; does not inspect files or reconcile operations.',
                    'refresh': 'Explicit worker observation; does not resume file movement.',
                    'export': 'Previously verified immutable artifact; downloads do not execute a worker.',
                    'provider': 'LIVE_BROKER' if self.coordinator.live_config else 'CLOSED'}}

    def close(self):
        self.coordinator.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
