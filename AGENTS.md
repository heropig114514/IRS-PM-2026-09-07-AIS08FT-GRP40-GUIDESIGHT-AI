# Engineering conventions

- Keep backend code, dependencies, configuration, tests and developer documentation in `/backend`; frontend code belongs in `/frontend`.
- Use Django + DRF + Uvicorn/ASGI. Follow AiInterviewer's organization without importing its interview business logic.
- API adapters validate inputs and format responses; application `services.py` owns business rules and the ORM owns persistence.
- Diagnose implementation, contracts and data flow first, then configuration/environment; investigate framework issues only with evidence.
- Log actionable context at important state transitions, external calls and exception branches. Never log keys, passwords or sensitive payloads.
- Do not introduce unauthorized implicit fallbacks or automatic retries. Reasonable parameter adjustments do not need individual approval: inspect implementation/configuration, explain rationale, impact and boundaries, record reproducible conditions, and retain the original path and comparisons. Never silently change conditions to claim improvement. Explain changes to seeds, splits or evaluation metrics; changing objectives or invalidating comparisons requires explicit user approval.
- Remove obsolete callers, configuration, comments and tests together with replaced implementations.
- Organize Markdown by current responsibilities, interfaces, configuration, operation and constraints. Keep chronological development logs, interim test counts and per-run commit/service status in structured reports; describe delivery checks in the conversation.
- Use English for development prose: identifiers, comments, declaration/variable indexes, logs, tool descriptions, Skill instructions and developer documentation. Preserve localized user-facing text, multilingual language-processing rules/test inputs, and exact external names/paths. Examples of user utterances may remain in their tested language; explain them in English.

## Documentation and checks

- Begin each changed code file with `Responsibilities, Implementation, Related Modules, Declaration Index, Variable Index`; state `None` when no declarations or variables apply.
- Document functions/methods with `Functionality, Inputs, Outputs, Logic, Constraints`, and classes with `Functionality, Logic, Constraints`. Explain rationale and limitations without fabricated citations.
- Index only implemented declarations, using locatable qualified method names; exclude imports and inherited methods that are not overridden.
- Update implementation, comments, indexes, tests and documentation together, and include them in the same commit when committing.
- From `/backend`, run `python manage.py check`, `python manage.py makemigrations --check --dry-run`, `python manage.py test` and `ruff check .`.
- The project currently has no `tools/check_docs.py`; review comments and indexes manually before delivery. If that checker is introduced, run it without bypassing failures.
- Report actual checks, unverified items and whether a commit was created. Passing tests does not establish real external-service behavior.
