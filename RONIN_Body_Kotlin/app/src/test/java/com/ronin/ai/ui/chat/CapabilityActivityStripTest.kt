package com.ronin.ai.ui.chat

import com.ronin.ai.network.AgentEvent
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class CapabilityActivityStripTest {
    @Test
    fun empty_activity_list_has_no_renderable_rows() {
        assertTrue(capabilityActivityRenderItems(emptyList()).isEmpty())
    }

    @Test
    fun basic_activity_renders_capability_and_state_without_guesses() {
        val activity = CapabilityActivity(
            capability = CapabilityKind.REASONING,
            state = CapabilityActivityState.ACTIVE,
            sequence = 2
        )

        assertEquals("Reasoning • Active", capabilityActivityLabel(activity))
        assertTrue(capabilityActivityDetails(activity).isEmpty())
        assertTrue(capabilityActivityCitationLabels(activity).isEmpty())
    }

    @Test
    fun optional_locality_and_retrieval_are_rendered_only_when_present() {
        val withoutOptionalFields = CapabilityActivity(
            capability = CapabilityKind.WEB_RETRIEVAL,
            state = CapabilityActivityState.SUCCESS
        )
        assertFalse(capabilityActivityDetails(withoutOptionalFields).contains("Local"))
        assertFalse(capabilityActivityDetails(withoutOptionalFields).any { it.startsWith("Retrieval:") })

        val withOptionalFields = withoutOptionalFields.copy(
            location = CapabilityLocation.LOCAL,
            retrieval = RetrievalMode.LEXICAL
        )
        val details = capabilityActivityDetails(withOptionalFields)
        assertTrue(details.contains("Local"))
        assertTrue(details.contains("Retrieval: Lexical"))
    }

    @Test
    fun recovery_is_rendered_only_when_structurally_present() {
        val activity = CapabilityActivity(
            capability = CapabilityKind.WEB_RETRIEVAL,
            state = CapabilityActivityState.ACTIVE
        )
        assertFalse(capabilityActivityDetails(activity).any { it.startsWith("Recovery:") })

        val recovered = activity.copy(
            recovery = CapabilityRecovery(RecoverySummary.RETRYING, sequence = 4)
        )
        val details = capabilityActivityDetails(recovered)
        assertTrue(details.contains("Recovery: Retrying"))
        assertTrue(details.contains("Recovery sequence: 4"))
    }

    @Test
    fun citations_are_only_rendered_after_existing_adapter_validation() {
        val invalidObservation = AgentEvent.Observation(
            step = 1,
            tool = "search_local_knowledge",
            ok = true,
            ms = 4,
            result = "[1] /private/db.sqlite (lines 1-2, chars 0-4)",
            semanticCapability = "local_knowledge_search",
            locality = "local",
            sequence = 5
        )
        val activity = CapabilityPresentationAdapter.fromObservation(
            invalidObservation,
            previous = null,
            sequence = 1
        )

        assertTrue(activity?.citations?.isEmpty() == true)
        assertTrue(capabilityActivityCitationLabels(activity!!).isEmpty())
    }
}
