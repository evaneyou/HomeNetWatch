package com.homenetwatch.agent

import org.junit.Assert.assertEquals
import org.junit.Test

class WizardTest {
    @Test
    fun aNewDeviceStartsByBeingNamed() {
        assertEquals(Wizard.NAME, Wizard.show(null, hasName = false, hasKey = false, approved = false))
    }

    @Test
    fun aNamedDeviceStartsOnRegisterButCanMoveToTheKey() {
        assertEquals(Wizard.REGISTER, Wizard.show(null, hasName = true, hasKey = false, approved = false))
        assertEquals(Wizard.KEY, Wizard.show(Wizard.KEY, hasName = true, hasKey = false, approved = false))
    }

    @Test
    fun stepsCannotBeSkipped() {
        assertEquals(Wizard.NAME, Wizard.show(Wizard.KEY, hasName = false, hasKey = false, approved = false))
        assertEquals(Wizard.KEY, Wizard.show(Wizard.APPROVE, hasName = true, hasKey = false, approved = false))
    }

    @Test
    fun withAKeyItWaitsForApprovalAndCanGoBack() {
        assertEquals(Wizard.APPROVE, Wizard.show(null, hasName = true, hasKey = true, approved = false))
        assertEquals(Wizard.NAME, Wizard.show(Wizard.NAME, hasName = true, hasKey = true, approved = false))
    }

    @Test
    fun anApprovedDeviceIsDoneWhateverIsAsked() {
        assertEquals(Wizard.DONE, Wizard.show(Wizard.NAME, hasName = true, hasKey = true, approved = true))
    }
}
