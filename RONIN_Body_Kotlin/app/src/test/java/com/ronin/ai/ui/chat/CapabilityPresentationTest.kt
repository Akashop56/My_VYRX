package com.ronin.ai.ui.chat

import com.ronin.ai.network.AgentEvent
import com.ronin.ai.network.AgentEventCodec
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class CapabilityPresentationTest {
    @Test
    fun execution_outcome_health_locality_and_retrieval_are_independent() {
        val remoteUnavailable = CapabilityActivity(
            capability = CapabilityKind.REASONING,
            state = CapabilityActivityState.FAILED,
            location = CapabilityLocation.REMOTE,
            health = CapabilityHealthState.UNAVAILABLE
        )
        val webAvailable = CapabilityActivity(
            capability = CapabilityKind.WEB_RETRIEVAL,
            state = CapabilityActivityState.SUCCESS,
            location = CapabilityLocation.REMOTE,
            health = CapabilityHealthState.AVAILABLE
        )
        val localKnowledge = CapabilityActivity(
            capability = CapabilityKind.LOCAL_KNOWLEDGE_SEARCH,
            state = CapabilityActivityState.SUCCESS,
            location = CapabilityLocation.LOCAL,
            health = CapabilityHealthState.AVAILABLE,
            retrieval = RetrievalMode.LEXICAL
        )

        assertEquals("Reasoning • Remote • Failed • Unavailable", remoteUnavailable.indicatorLabel)
        assertEquals("Web • Remote • Success • Available", webAvailable.indicatorLabel)
        assertEquals("Local Knowledge • Local • Success • Available", localKnowledge.indicatorLabel)
        assertTrue(listOf(remoteUnavailable, webAvailable, localKnowledge).any {
            it.capability == CapabilityKind.WEB_RETRIEVAL && it.health == CapabilityHealthState.AVAILABLE
        })
    }

    @Test
    fun reasoning_activity_does_not_guess_locality() {
        val active = CapabilityPresentationAdapter.fromThinking(
            AgentEvent.Thinking(0, "call", "Calling provider — reasoning…"),
            1
        )
        assertNotNull(active)
        assertEquals(CapabilityKind.REASONING, active?.capability)
        assertNull(active?.location)
        val finished = CapabilityPresentationAdapter.completeReasoning(active, success = true, sequence = 2)
        assertEquals(CapabilityActivityState.SUCCESS, finished?.state)
        assertNull(finished?.location)
    }

    @Test
    fun actual_retrieval_method_replaces_requested_mode_and_supports_citations() {
        val call = AgentEvent.ToolCall(
            step = 1,
            tool = "search_local_knowledge",
            label = "Local Knowledge",
            args = JSONObject().put("mode", "hybrid"),
            device = false,
            thought = null,
            action = null,
            callId = "call-1",
            semanticCapability = "local_knowledge_search",
            locality = "local",
            sequence = 10
        )
        val active = CapabilityPresentationAdapter.fromToolCall(call, 1)
        assertNotNull(active)
        assertNull("requested hybrid must not become actual retrieval", active?.retrieval)

        val observation = AgentEvent.Observation(
            step = 1,
            tool = "search_local_knowledge",
            ok = true,
            ms = 9,
            result = "[1] notes/project.md (lines 120-148, chars 40-90)\nprivate chunk text",
            callId = "call-1",
            outcome = "success",
            requestedMode = "hybrid",
            retrievalMethod = "lexical_fts5",
            semanticCapability = "local_knowledge_search",
            locality = "local",
            sequence = 11
        )
        val completed = CapabilityPresentationAdapter.fromObservation(observation, active, 2)
        assertEquals(CapabilityActivityState.SUCCESS, completed?.state)
        assertEquals(RetrievalMode.LEXICAL, completed?.retrieval)
        assertEquals(1, completed?.citations?.size)
        assertEquals("notes/project.md", completed?.citations?.first()?.source)
        assertEquals("lines 120-148", completed?.citations?.first()?.locationLabel)
        assertEquals(RetrievalMode.LEXICAL, completed?.citations?.first()?.retrieval)
        assertFalse(completed?.citations?.toString()?.contains("private chunk text") == true)
    }

    @Test
    fun missing_actual_retrieval_omits_badge_and_partial_is_explicit_only() {
        val event = AgentEvent.Observation(
            1, "search_local_knowledge", true, 4,
            "[1] notes/a.md (lines 2, chars 0-4)",
            callId = "call-2",
            outcome = "partial_success",
            semanticCapability = "local_knowledge_search",
            locality = "local",
            sequence = 20
        )
        val activity = CapabilityPresentationAdapter.fromObservation(event, null, 1)
        assertEquals(CapabilityActivityState.PARTIAL, activity?.state)
        assertNull(activity?.retrieval)
        assertTrue(activity?.indicatorLabel?.contains("Lexical") != true)
        assertTrue(activity?.citations?.first()?.retrieval == null)
    }

    @Test
    fun citation_validation_rejects_absolute_and_traversal_paths() {
        assertTrue(CapabilityPresentationAdapter.parseKnowledgeCitations(null).isEmpty())
        assertTrue(CapabilityPresentationAdapter.parseKnowledgeCitations("No local matches").isEmpty())
        assertTrue(CapabilityPresentationAdapter.parseKnowledgeCitations(
            "[1] /private/db.sqlite (lines 1-2, chars 0-4)"
        ).isEmpty())
        assertTrue(CapabilityPresentationAdapter.parseKnowledgeCitations(
            "[1] ../private/db.sqlite (lines 1-2, chars 0-4)"
        ).isEmpty())
    }

    @Test
    fun recovery_uses_structured_transition_and_never_strategy_words() {
        val retry = CapabilityPresentationAdapter.fromSelfCorrection(
            AgentEvent.SelfCorrection(
                step = 0,
                tool = "search",
                reason = "remote retrieval failed",
                strategy = "try an alternative capability",
                attempt = 1,
                callId = "call-1",
                transitionType = "retry",
                semanticCapability = "web_retrieval",
                locality = "remote",
                sequence = 30
            ),
            sequence = 4
        )
        assertEquals(RecoverySummary.RETRYING, retry?.recovery?.summary)
        assertEquals("Retrying", retry?.recovery?.summary?.label)

        val replacementWithoutIdentities = CapabilityPresentationAdapter.fromSelfCorrection(
            AgentEvent.SelfCorrection(
                0, "search", "failed", "alternative", 1,
                transitionType = "capability_replacement",
                semanticCapability = "web_retrieval",
                sequence = 31
            ),
            5
        )
        assertNull(replacementWithoutIdentities)

        val replacement = CapabilityPresentationAdapter.fromSelfCorrection(
            AgentEvent.SelfCorrection(
                0, "search", "failed", "retry", 1,
                transitionType = "capability_replacement",
                semanticCapability = "web_retrieval",
                previousCandidate = "provider-a",
                selectedCandidate = "provider-b",
                outcome = "success",
                locality = "remote",
                sequence = 32
            ),
            6
        )
        assertEquals(RecoverySummary.CAPABILITY_CHANGED, replacement?.recovery?.summary)
        assertEquals("Capability changed", replacement?.recovery?.summary?.label)

        val fallback = CapabilityPresentationAdapter.fromSelfCorrection(
            AgentEvent.SelfCorrection(
                0, null, "no provider", "offline alternative", 0,
                transitionType = "fallback_proposal",
                semanticCapability = "reasoning",
                sequence = 33
            ),
            7
        )
        assertEquals(RecoverySummary.FALLBACK_AVAILABLE, fallback?.recovery?.summary)
    }

    @Test
    fun structured_control_boundary_does_not_hide_legitimate_json_or_partial_text() {
        assertEquals("", CapabilityPresentationAdapter.sanitizeAnswer(
            "{\"type\":\"tool_call\",\"tool\":\"open_app\"}"
        ))
        assertEquals("", CapabilityPresentationAdapter.sanitizeAnswer(
            "{\"route\":\"agent_action\",\"action\":{\"tool\":\"open_app\"}}"
        ))
        assertTrue(CapabilityPresentationAdapter.isInternalMetadata("{\"type\":\"observation\"}"))
        assertEquals("The safe answer", CapabilityPresentationAdapter.sanitizeAnswer(
            "{\"response\":\"The safe answer\",\"capability\":\"reasoning\"}"
        ))
        val userJson = "{\"action\":\"explain\",\"tool\":\"camera\",\"capability\":\"user-data\"}"
        assertEquals(userJson, CapabilityPresentationAdapter.sanitizeAnswer(userJson))
        val code = "if (x) {\n  return {\"action\": \"keep\"}\n"
        assertEquals(code, CapabilityPresentationAdapter.sanitizeAnswer(code))
        assertEquals(
            "Tool result received",
            CapabilityPresentationAdapter.safeObservationText(
                AgentEvent.Observation(0, "search", true, 3, "{\"type\":\"observation\"}")
            )
        )
    }

    @Test
    fun event_codec_preserves_stable_identity_metadata() {
        val event = AgentEventCodec.decode(
            "tool_call",
            "{\"type\":\"tool_call\",\"seq\":42,\"step\":2,\"tool\":\"search_local_knowledge\"," +
                "\"call_id\":\"call-7\",\"semantic_capability\":\"local_knowledge_search\",\"locality\":\"local\"}"
        ) as AgentEvent.ToolCall
        assertEquals(42, event.sequence)
        assertEquals("call-7", event.callId)
        assertEquals("local_knowledge_search", event.semanticCapability)
        assertEquals("local", event.locality)
    }

    @Test
    fun request_scoped_ledger_deduplicates_replays_but_preserves_distinct_attempts() {
        val ledger = RequestEventLedger()
        val first = AgentEvent.ToolCall(
            1, "search_local_knowledge", "Local", JSONObject(), false, null, null,
            callId = "call-a", semanticCapability = "local_knowledge_search", locality = "local", sequence = 50
        )
        val replay = first.copy()
        val secondAttempt = first.copy(callId = "call-b", sequence = 51)
        assertTrue(ledger.accept(first))
        assertFalse(ledger.accept(replay))
        assertTrue(ledger.accept(secondAttempt))
        val recovery = AgentEvent.SelfCorrection(
            1, "search", "failed", "retry", 1, callId = "call-a"
        )
        assertTrue(ledger.accept(recovery))
        assertFalse(ledger.accept(recovery.copy(sequence = 99)))
        assertTrue(ledger.accept(recovery.copy(attempt = 2, sequence = 100)))
        ledger.reset()
        assertTrue("a new request may reuse transport sequence values", ledger.accept(first))
    }

    @Test
    fun unknown_or_unstructured_tools_are_not_invented() {
        val unknown = AgentEvent.ToolCall(
            step = 0,
            tool = "future_tool",
            label = "Future Tool",
            args = JSONObject(),
            device = false,
            thought = null,
            action = null
        )
        assertNull(CapabilityPresentationAdapter.fromToolCall(unknown, 1))
        assertNull(CapabilityPresentationAdapter.fromSelfCorrection(
            AgentEvent.SelfCorrection(0, "future_tool", "alternative", "alternative", 1), 2
        ))
    }
}
