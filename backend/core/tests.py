"""Responsibilities: Verify the real test database, health-endpoint failure semantics, and
error-response boundaries.
Implementation: TestCase uses isolated SQLite; replace the database cursor only in the failure-path
test.
Related Modules: core.views/errors and config.base; no external database or GitHub access.
Declaration Index:
- HealthTests — health endpoint success, failure, and method restrictions.
- HealthTests.test_database_health — probe the real test database.
- HealthTests.test_database_failure — simulate DatabaseError, retain 503, and record a log.
- HealthTests.test_post_is_rejected — reject writes to the health endpoint.
- HealthTests.test_admin_requires_login — redirect anonymous admin requests to login.
- ErrorHandlerTests — known and unknown DRF exceptions.
- ErrorHandlerTests.test_validation_error_keeps_standard_response — preserve the DRF 400 contract.
- ErrorHandlerTests.test_unexpected_error_is_logged_and_redacted — return fixed 500 and retain
diagnostic logging.
Variable Index: None.
"""

from unittest.mock import patch

from django.db import DatabaseError
from django.test import SimpleTestCase, TestCase
from rest_framework.exceptions import ValidationError

from .errors import api_exception_handler


class HealthTests(TestCase):
    """Functionality: Check the public health endpoint and admin authentication boundary.
    Logic: Use Django's test client and an isolated database; replace the cursor only in the
    database-failure test.
    Constraints: Does not prove PostgreSQL or a real ASGI network service is deployed.
    """

    def test_database_health(self):
        """Functionality: Verify an anonymous request actually probes the test database.
        Inputs: self.client and Django's isolated SQLite; no external parameters or mocks.
        Outputs: Assertions raise on failure; otherwise returns None.
        Logic: GET the health endpoint and check the complete 200 response.
        Constraints: Proves only test-database connectivity; startup checks cover the development
        database separately.
        """
        response = self.client.get("/api/health/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "database": "sqlite"})

    def test_database_failure(self):
        """Functionality: Verify the database-failure log and 503 contract.
        Inputs: Test client; replace core.views.connection.cursor with a DatabaseError-raising stub.
        Outputs: Assert a credential-free response and error log; perform no database writes.
        Logic: Make the request while capturing error logs, then check status and code.
        Constraints: A simulated failure is not a real PostgreSQL disconnection experiment.
        """
        with (
            patch("core.views.connection.cursor", side_effect=DatabaseError("probe failed")),
            self.assertLogs("core.views", level="ERROR") as logs,
        ):
            response = self.client.get("/api/health/")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "error", "code": "database_unavailable"})
        self.assertIn("health database probe failed", logs.output[0])

    def test_post_is_rejected(self):
        """Functionality: Confirm the health endpoint cannot be used for writes.
        Inputs: Anonymous test client and empty POST; no mocks.
        Outputs: Assert HTTP 405; otherwise returns None.
        Logic: Verify DRF's method restriction directly.
        Constraints: Does not replace CSRF and resource-permission tests for future business
        endpoints.
        """
        self.assertEqual(self.client.post("/api/health/", {}).status_code, 405)

    def test_admin_requires_login(self):
        """Functionality: Verify that the Django admin entry point requires login.
        Inputs: Unauthenticated self.client; no mocks or admin account.
        Outputs: Assert a redirect to the login page with a next parameter.
        Logic: Do not follow the redirect, so the login response cannot be mistaken for authorized
        access.
        Constraints: Does not verify business-account registration or frontend login flow.
        """
        self.assertRedirects(
            self.client.get("/admin/"), "/admin/login/?next=/admin/", fetch_redirect_response=False
        )


class ErrorHandlerTests(SimpleTestCase):
    """Functionality: Check that the API exception handler preserves status codes and limits public
    details.
    Logic: Pass DRF and ordinary exceptions directly to the real handler while capturing logs.
    Constraints: No database is required; this does not cover every future business exception.
    """

    def test_validation_error_keeps_standard_response(self):
        """Functionality: Confirm that validation errors retain the standard DRF contract.
        Inputs: ValidationError and empty context; no mocks.
        Outputs: Assert 400 and detail; no side effects.
        Logic: Check the default handler result so unified error handling does not override client
        errors.
        Constraints: No real business serializer is called.
        """
        response = api_exception_handler(ValidationError({"name": "required"}), {})
        self.assertEqual(response.status_code, 400)
        self.assertIn("name", response.data)

    def test_unexpected_error_is_logged_and_redacted(self):
        """Functionality: Verify that unknown exceptions return fixed 500 and record their location.
        Inputs: Synthetic RuntimeError, empty context, and captured error logs; no mocks.
        Outputs: Assert status, fixed response, and error log; original exception text is not
        exposed.
        Logic: Inspect handler output and log event directly.
        Constraints: The synthetic exception contains no sensitive information and does not verify
        real external dependencies.
        """
        with self.assertLogs("core.errors", level="ERROR") as logs:
            response = api_exception_handler(RuntimeError("synthetic failure"), {})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(
            response.data, {"code": "internal_error", "detail": "Internal server error."}
        )
        self.assertIn("unhandled API exception view=unknown", logs.output[0])
