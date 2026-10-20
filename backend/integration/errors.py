"""Responsibilities: Carry public integration failures without exception bodies or media.
Implementation: Stable code/detail/status attributes shared by the two ASGI processes.
Related Modules: integration.protocol/services; integration.inference.services.
Declaration Index: IntegrationError; IntegrationError.__init__.
Variable Index: None.
"""


class IntegrationError(Exception):
    """Functionality: Represent an explicit, safe integration failure.
    Logic: Preserve public fields only. Constraints: no retries or provider substitution.
    """

    def __init__(self, code, detail, status=503):
        """Functionality: Initialize stable failure fields.
        Inputs: public code/detail/status. Outputs: exception instance.
        Logic: retain explicitly supplied safe text. Constraints: never supply raw exceptions.
        """
        super().__init__(detail)
        self.code, self.detail, self.status = code, detail, status
