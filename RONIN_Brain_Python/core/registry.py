"""Active Capability Registry — in-memory, thread-safe capability management.

Centralises the lifecycle of :class:`~core.capabilities.CapabilityDescriptor`
instances and their associated :class:`~core.capabilities.HealthTracker`
state machines.  Every capability that the orchestrator might route to is
registered here; health updates flow through policy-driven trackers rather
than ad-hoc flag flipping.

Design principles
-----------------
* **Identity is the key.**  ``capability_id`` is the unique key.  Multiple
  implementations may share the same :class:`SemanticCapabilityType` (e.g.
  two different REASONING backends).
* **Health defaults to UNKNOWN.**  Registration always resets health so
  callers cannot smuggle in stale state.  The first execution outcome
  drives the first real transition.
* **State separation.**  The :class:`CapabilityDescriptor` is the *static
  declaration* (identity, dependencies, operational characteristics).  The
  :class:`HealthTracker` is the *dynamic runtime state* (consecutive
  failure counts, recovery windows).  The registry pairs them and keeps
  them in sync.
* **Thread-safe.**  All public methods acquire ``self._lock``.  This
  matches the existing ``threading.Lock`` convention used by
  :class:`~core.provider_manager.ProviderManager`,
  :class:`~core.action_log.ActionLog`, and
  :class:`~core.state_manager.StateManager`.
* **No persistence.**  Pure in-memory storage.  Persistence (Redis, SQLite,
  etc.) is a future concern.
"""
from __future__ import annotations

import copy
import threading
from typing import Any

from core.capabilities import (
    CanonicalFailureClass,
    CapabilityDescriptor,
    CapabilityHealth,
    HealthPolicy,
    HealthTracker,
    SemanticCapabilityType,
)


class CapabilityNotFoundError(KeyError):
    """Raised when a capability ID is not found in the registry."""


class CapabilityRegistry:
    """In-memory, thread-safe registry of active capabilities.

    Typical lifecycle::

        registry = CapabilityRegistry()

        # Register capabilities at startup
        registry.register(CapabilityDescriptor(
            id="llm-groq",
            capability_type=SemanticCapabilityType.REASONING,
            description="Groq LLaMA reasoning backend",
            requires_internet=True,
            requires_auth=True,
            is_local=False,
        ))

        # Record execution outcomes during the agent loop
        registry.update_health("llm-groq", success=True)
        registry.update_health("llm-groq", success=False,
                               failure_class=CanonicalFailureClass.TRANSIENT)

        # Query for routing decisions
        available = registry.query_by_health(CapabilityHealth.AVAILABLE)
        reasoners = registry.query_by_semantic_type(SemanticCapabilityType.REASONING)
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._descriptors: dict[str, CapabilityDescriptor] = {}
        self._trackers: dict[str, HealthTracker] = {}

    # -- core CRUD -----------------------------------------------------------

    def register(
        self,
        descriptor: CapabilityDescriptor,
        policy: HealthPolicy | None = None,
    ) -> CapabilityDescriptor:
        """Register (or replace) a capability.

        Health is **always** reset to ``UNKNOWN`` on registration so stale
        state from a previous incarnation cannot leak through.  If a
        capability with the same ``id`` already exists it is silently
        replaced (idempotent re-registration).

        Returns a *copy* of the stored descriptor (with ``health=UNKNOWN``).
        """
        # Deep-copy so the caller's mutable fields (metadata, lists) cannot
        # be mutated after registration.
        stored = descriptor.model_copy(deep=True)
        stored.health = CapabilityHealth.UNKNOWN

        tracker = HealthTracker(
            capability_id=stored.id,
            policy=policy,
            initial_health=CapabilityHealth.UNKNOWN,
        )

        with self._lock:
            self._descriptors[stored.id] = stored
            self._trackers[stored.id] = tracker

        return stored.model_copy(deep=True)

    def unregister(self, capability_id: str) -> bool:
        """Remove a capability from the registry.

        Returns ``True`` if the capability existed and was removed,
        ``False`` if it was not found.
        """
        with self._lock:
            existed = capability_id in self._descriptors
            self._descriptors.pop(capability_id, None)
            self._trackers.pop(capability_id, None)
        return existed

    def get(self, capability_id: str) -> CapabilityDescriptor | None:
        """Retrieve a capability descriptor by ID.

        The returned descriptor's ``health`` field reflects the *current*
        tracker state.  Returns ``None`` if not found.
        """
        with self._lock:
            descriptor = self._descriptors.get(capability_id)
            if descriptor is None:
                return None
            tracker = self._trackers.get(capability_id)
            # Sync health from tracker into the descriptor snapshot
            snapshot = descriptor.model_copy(deep=True)
            if tracker is not None:
                snapshot.health = tracker.current_health
            return snapshot

    def list(self) -> list[CapabilityDescriptor]:
        """List all registered capabilities with their current health.

        Returns a list of *copies* — mutating them has no effect on the
        registry.
        """
        with self._lock:
            result: list[CapabilityDescriptor] = []
            for cap_id, descriptor in self._descriptors.items():
                snapshot = descriptor.model_copy(deep=True)
                tracker = self._trackers.get(cap_id)
                if tracker is not None:
                    snapshot.health = tracker.current_health
                result.append(snapshot)
        return result

    # -- health updates ------------------------------------------------------

    def update_health(
        self,
        capability_id: str,
        *,
        success: bool = True,
        failure_class: CanonicalFailureClass | None = None,
    ) -> CapabilityHealth:
        """Record an execution outcome and update health via the policy.

        * ``success=True``  → delegates to :meth:`HealthTracker.record_success`
        * ``success=False`` → delegates to :meth:`HealthTracker.record_failure`
          (requires ``failure_class``)

        Returns the new :class:`CapabilityHealth` value.

        Raises :class:`CapabilityNotFoundError` if the ID is not registered.
        """
        with self._lock:
            tracker = self._trackers.get(capability_id)
            if tracker is None:
                if capability_id not in self._descriptors:
                    raise CapabilityNotFoundError(
                        f"Capability not registered: {capability_id!r}"
                    )
                # Should not happen (descriptor exists but no tracker), but
                # be defensive.
                raise CapabilityNotFoundError(
                    f"Capability not registered: {capability_id!r}"
                )

        # Tracker operations are fast and don't need the registry lock held
        # (the tracker's own state is only accessed through this single
        # reference).  Releasing the lock early reduces contention.
        if success:
            return tracker.record_success()
        else:
            if failure_class is None:
                raise ValueError(
                    "failure_class is required when success=False"
                )
            return tracker.record_failure(failure_class)

    # -- queries (side-effect-free) ------------------------------------------

    def query_by_semantic_type(
        self, capability_type: SemanticCapabilityType
    ) -> list[CapabilityDescriptor]:
        """Find all capabilities of a given semantic type.

        Returns snapshots with current health.  Empty list if none match.
        """
        with self._lock:
            result: list[CapabilityDescriptor] = []
            for cap_id, descriptor in self._descriptors.items():
                if descriptor.capability_type == capability_type:
                    snapshot = descriptor.model_copy(deep=True)
                    tracker = self._trackers.get(cap_id)
                    if tracker is not None:
                        snapshot.health = tracker.current_health
                    result.append(snapshot)
        return result

    def query_by_health(
        self, health: CapabilityHealth
    ) -> list[CapabilityDescriptor]:
        """Find all capabilities with a specific health state.

        Returns snapshots with current health.  Empty list if none match.
        """
        with self._lock:
            result: list[CapabilityDescriptor] = []
            for cap_id, descriptor in self._descriptors.items():
                tracker = self._trackers.get(cap_id)
                current = tracker.current_health if tracker else descriptor.health
                if current == health:
                    snapshot = descriptor.model_copy(deep=True)
                    snapshot.health = current
                    result.append(snapshot)
        return result

    def query_by_locality(
        self, *, is_local: bool
    ) -> list[CapabilityDescriptor]:
        """Find capabilities by locality (local vs. remote).

        Returns snapshots with current health.  Empty list if none match.
        """
        with self._lock:
            result: list[CapabilityDescriptor] = []
            for cap_id, descriptor in self._descriptors.items():
                if descriptor.is_local == is_local:
                    snapshot = descriptor.model_copy(deep=True)
                    tracker = self._trackers.get(cap_id)
                    if tracker is not None:
                        snapshot.health = tracker.current_health
                    result.append(snapshot)
        return result

    # -- tracker access (for advanced callers) -------------------------------

    def get_tracker(self, capability_id: str) -> HealthTracker | None:
        """Access the :class:`HealthTracker` for a capability.

        Returns ``None`` if the capability is not registered.  Callers that
        need direct tracker access (e.g. for custom policy evaluation) can
        use this, but ``update_health`` is the preferred interface.
        """
        with self._lock:
            tracker = self._trackers.get(capability_id)
            # Return the actual reference — the tracker is not shared
            # outside the registry, so this is safe.
            return tracker

    # -- introspection -------------------------------------------------------

    @property
    def size(self) -> int:
        """Number of registered capabilities."""
        with self._lock:
            return len(self._descriptors)

    def contains(self, capability_id: str) -> bool:
        """Check whether a capability ID is registered."""
        with self._lock:
            return capability_id in self._descriptors

    def registered_ids(self) -> list[str]:
        """Return a snapshot list of all registered capability IDs."""
        with self._lock:
            return list(self._descriptors.keys())
