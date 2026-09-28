"""Phase 1 table ownership. Each table has exactly one owner module."""

TABLE_OWNERS: dict[str, str] = {
    "v2_relationships": "relationship_v2.persistence.repository:get_or_create_relationship",
    "v2_memory_facts": "relationship_v2.persistence.repository:create_memory_fact",
    "v2_memory_episodes": "relationship_v2.persistence.repository:create_memory_episode",
    "v2_conversations": "relationship_v2.persistence.repository:create_conversation",
    "v2_conversation_turns": "relationship_v2.persistence.repository:create_conversation_turn",
    "v2_events": "relationship_v2.persistence.repository:create_relationship_event",
    "v2_commerce_refs": "relationship_v2.persistence.repository:create_commerce_ref",
    "v2_relationship_snapshots": "relationship_v2.services.relationship_context:persist_snapshot",
    "v2_engagement_signals": "relationship_v2.persistence.repository:record_engagement_signal",
    "v2_processed_events": "relationship_v2.persistence.repository:mark_event_processed",
    "v2_open_loops": "relationship_v2.persistence.repository:create_open_loop",
    "v2_intimate_history": "relationship_v2.persistence.repository:record_intimate_signal",
}
