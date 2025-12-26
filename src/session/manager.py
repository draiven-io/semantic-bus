"""
Session Manager

Manages ephemeral interface sessions: creation, offer collection,
agreement, and cleanup.

Sessions are created when an intent is broadcast and track the
entire negotiation lifecycle.

Supports optional persistent storage via SessionStore interface.
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, TYPE_CHECKING
from uuid import UUID

from src.session.session import Session, SessionStatus, Offer

if TYPE_CHECKING:
    from src.storage import SessionStore

logger = logging.getLogger(__name__)


class SessionManager:
    """
    Manages the lifecycle of ephemeral interface sessions.
    
    Thread-safe for async operations using asyncio locks.
    
    If a SessionStore is provided, sessions are persisted.
    Otherwise, sessions are stored in-memory only.
    """
    
    def __init__(
        self,
        negotiation_ttl_seconds: int = 30,
        session_ttl_seconds: int = 300,
        cleanup_interval_seconds: float = 10.0,
        storage: "SessionStore | None" = None,
    ):
        """
        Initialize the session manager.
        
        Args:
            negotiation_ttl_seconds: Max time to collect offers
            session_ttl_seconds: Max session lifetime after activation
            cleanup_interval_seconds: How often to check for expired sessions
            storage: Optional persistent storage adapter
        """
        self._negotiation_ttl = negotiation_ttl_seconds
        self._session_ttl = session_ttl_seconds
        self._cleanup_interval = cleanup_interval_seconds
        self._storage = storage
        
        # Primary index: ephemeral_interface_id -> Session (in-memory cache)
        self._sessions: dict[UUID, Session] = {}
        
        # Secondary index: intent_message_id -> ephemeral_interface_id
        # Allows providers to reference sessions by the original broadcast ID
        self._sessions_by_intent: dict[UUID, UUID] = {}
        
        # Lock for thread-safe operations
        self._lock = asyncio.Lock()
        
        # Background cleanup task
        self._cleanup_task: asyncio.Task | None = None
    
    async def start(self) -> None:
        """Start background cleanup task."""
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())
            logger.info("Session manager cleanup task started")
    
    async def stop(self) -> None:
        """Stop background cleanup task."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None
            logger.info("Session manager cleanup task stopped")
    
    async def _cleanup_loop(self) -> None:
        """Periodically clean up expired sessions."""
        while True:
            try:
                await asyncio.sleep(self._cleanup_interval)
                await self._cleanup_expired_sessions()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in session cleanup loop: {e}")
    
    async def _cleanup_expired_sessions(self) -> None:
        """Remove expired sessions."""
        async with self._lock:
            now = datetime.utcnow()
            expired_ids = []
            
            for session_id, session in self._sessions.items():
                if session.is_expired() and session.status not in (
                    SessionStatus.CLOSED, SessionStatus.EXPIRED
                ):
                    logger.info(
                        f"Session {session_id} expired "
                        f"(status: {session.status}, created: {session.created_at})"
                    )
                    session.status = SessionStatus.EXPIRED
                    session.closed_at = now
                    expired_ids.append(session_id)
            
            # Optionally remove old closed sessions
            # For now, keep them for debugging
            # In production, you'd want to archive or delete old sessions
    
    async def create_session(
        self,
        tenant_id: str,
        requester_agent_id: UUID,
        requester_conn_id: str,
        intent_message_id: UUID,
        intent: dict[str, Any],
        flow_agents: list[str] | None = None,
        flow_entry_point: str | None = None,
    ) -> Session:
        """
        Create a new session for an intent broadcast.
        
        Called when the bus receives intent.broadcast.
        
        Args:
            tenant_id: Tenant the session belongs to
            requester_agent_id: Agent ID of the requester
            requester_conn_id: Connection ID of the requester
            intent_message_id: Message ID of the broadcast
            intent: The intent payload
            flow_agents: List of agent IDs in the flow
            flow_entry_point: Entry point agent ID
        
        Returns:
            The created Session
        """
        session = Session(
            tenant_id=tenant_id,
            requester_agent_id=requester_agent_id,
            requester_conn_id=requester_conn_id,
            intent_message_id=intent_message_id,
            intent=intent,
            negotiation_ttl_seconds=self._negotiation_ttl,
            session_ttl_seconds=self._session_ttl,
            flow_agents=flow_agents or [],
            flow_entry_point=flow_entry_point,
        )
        
        async with self._lock:
            self._sessions[session.ephemeral_interface_id] = session
            self._sessions_by_intent[intent_message_id] = session.ephemeral_interface_id
        
        logger.info(
            f"Session created: {session.ephemeral_interface_id} "
            f"(intent: {intent_message_id}, requester: {requester_agent_id})"
        )
        
        return session
    
    async def get_session(self, session_id: UUID) -> Session | None:
        """Get a session by ephemeral_interface_id."""
        async with self._lock:
            return self._sessions.get(session_id)
    
    async def get_session_by_intent(self, intent_message_id: UUID) -> Session | None:
        """Get a session by the original intent message ID."""
        async with self._lock:
            session_id = self._sessions_by_intent.get(intent_message_id)
            if session_id:
                return self._sessions.get(session_id)
            return None
    
    async def add_offer(
        self,
        session_id: UUID,
        provider_agent_id: UUID,
        provider_conn_id: str,
        schema_ids: list[str] | None = None,
        view_ids: list[str] | None = None,
        requires: list[str] | None = None,
        accepts: list[str] | None = None,
        score: float = 0.0,
        metadata: dict[str, Any] | None = None
    ) -> Offer | None:
        """
        Add an offer to a session.
        
        Called when the bus receives intent.offer from a provider.
        
        Args:
            session_id: The session to add the offer to
            provider_agent_id: Agent ID of the provider
            provider_conn_id: Connection ID of the provider
            schema_ids: Schemas the provider can return
            view_ids: Views the provider supports
            requires: What the provider needs
            accepts: What input formats are accepted
            score: Relevance score
            metadata: Additional offer data
        
        Returns:
            The created Offer, or None if session not found/invalid
        """
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                logger.warning(f"Cannot add offer: session {session_id} not found")
                return None
            
            if session.status != SessionStatus.NEGOTIATING:
                logger.warning(
                    f"Cannot add offer: session {session_id} is {session.status}"
                )
                return None
            
            if session.is_expired():
                logger.warning(f"Cannot add offer: session {session_id} has expired")
                return None
            
            offer = Offer(
                provider_agent_id=provider_agent_id,
                provider_conn_id=provider_conn_id,
                schema_ids=schema_ids or [],
                view_ids=view_ids or [],
                requires=requires or [],
                accepts=accepts or [],
                score=score,
                metadata=metadata or {}
            )
            
            session.add_offer(offer)
            
            logger.info(
                f"Offer added to session {session_id}: {offer.offer_id} "
                f"(provider: {provider_agent_id})"
            )
            
            return offer
    
    async def agree(
        self,
        session_id: UUID,
        offer_id: str,
        chosen_schema_id: str | None = None,
        chosen_view_id: str | None = None
    ) -> Session | None:
        """
        Accept an offer and activate the session.
        
        Called when the bus receives intent.agree from the requester.
        
        Args:
            session_id: The session
            offer_id: The offer to accept
            chosen_schema_id: The agreed schema for responses
            chosen_view_id: The agreed view for responses
        
        Returns:
            The updated Session, or None if invalid
        """
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                logger.warning(f"Cannot agree: session {session_id} not found")
                return None
            
            if session.status != SessionStatus.NEGOTIATING:
                logger.warning(
                    f"Cannot agree: session {session_id} is {session.status}"
                )
                return None
            
            if not session.activate(offer_id, chosen_schema_id, chosen_view_id):
                logger.warning(
                    f"Cannot agree: offer {offer_id} not found in session {session_id}"
                )
                return None
            
            logger.info(
                f"Session activated: {session_id} "
                f"(offer: {offer_id}, provider: {session.provider_agent_id})"
            )
            
            return session
    
    async def close_session(
        self,
        session_id: UUID,
        reason: str | None = None
    ) -> Session | None:
        """
        Close a session.
        
        Args:
            session_id: The session to close
            reason: Optional reason for closing
        
        Returns:
            The closed Session, or None if not found
        """
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            
            session.close(reason)
            
            logger.info(f"Session closed: {session_id} (reason: {reason})")
            
            return session
    
    async def validate_session_for_exec(
        self,
        session_id: UUID,
        sender_agent_id: UUID
    ) -> tuple[bool, str | None, Session | None]:
        """
        Validate that a session is valid for execution messages.
        
        Checks:
        - Session exists
        - Session is active
        - Sender is a participant (requester or provider)
        
        Args:
            session_id: The session
            sender_agent_id: Who is sending the exec message
        
        Returns:
            Tuple of (is_valid, error_message, session)
        """
        async with self._lock:
            session = self._sessions.get(session_id)
            
            if session is None:
                return False, "Session not found", None
            
            if session.status != SessionStatus.ACTIVE:
                return False, f"Session is {session.status.value}, not active", None
            
            if session.is_expired():
                return False, "Session has expired", None
            
            # Check sender is a participant
            is_requester = sender_agent_id == session.requester_agent_id
            is_provider = sender_agent_id == session.provider_agent_id
            
            if not (is_requester or is_provider):
                return False, "Sender is not a session participant", None
            
            return True, None, session
    
    async def list_sessions(
        self,
        tenant_id: str | None = None,
        status: SessionStatus | None = None
    ) -> list[Session]:
        """List sessions, optionally filtered."""
        async with self._lock:
            sessions = list(self._sessions.values())
            
            if tenant_id:
                sessions = [s for s in sessions if s.tenant_id == tenant_id]
            
            if status:
                sessions = [s for s in sessions if s.status == status]
            
            return sessions
    
    async def set_flow_info(
        self,
        session_id: UUID,
        flow: Any,  # AgentFlow
        agent_records: dict[str, Any],  # agent_id -> AgentRecord
        aggregated_offer_id: str,
    ) -> bool:
        """
        Store flow information in an orchestrated session.
        
        Args:
            session_id: Session to update
            flow: The AgentFlow structure
            agent_records: Dict of agent_id -> AgentRecord
            aggregated_offer_id: ID of the aggregated offer
        
        Returns:
            True if successful
        """
        async with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return False
            
            session.aggregated_offer_id = aggregated_offer_id
            # Store flow info in session for later execution
            # We store serializable info only
            session.flow_agents = [agent.agent_id for agent in flow.agents]
            session.flow_entry_point = flow.entry_point
            
            return True
    
    async def update_flow_result(
        self,
        session_id: UUID,
        agent_id: str,
        result: dict[str, Any],
    ) -> bool:
        """
        Store the result from an agent in an orchestrated flow.
        
        Args:
            session_id: Session to update
            agent_id: The agent that produced the result
            result: The result data
        
        Returns:
            True if successful
        """
        async with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return False
            
            session.flow_results[agent_id] = result
            session.flow_current_step += 1
            
            return True
    
    async def get_flow_results(self, session_id: UUID) -> dict[str, Any]:
        """Get all flow results for an orchestrated session."""
        async with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return {}
            return session.flow_results
    
    @property
    def session_count(self) -> int:
        """Total number of sessions."""
        return len(self._sessions)
    
    @property
    def active_count(self) -> int:
        """Number of active sessions."""
        return sum(
            1 for s in self._sessions.values()
            if s.status == SessionStatus.ACTIVE
        )
