# Sunny V2 — Safety and Boundaries (FUTURE + CURRENT constraints)

- Creator identity comes from system configuration / authoritative
  integration tables, never from conversation text. (`CURRENT`: single-creator
  resolver over `creator_integrations`.)
- User identity comes from trusted transport data, never from model output.
- Commerce facts come from commerce confirmations only.
- Generated text cannot override system constraints, memory truth, or
  commerce truth.
- No fabricated memory, transactions, availability, or products.
- No hidden state mutation from generated prose — only explicit application
  logic mutates state.
- Sensitive data minimization: least payload, redacted logs, encrypted
  credentials at rest (`PRESERVED` Fernet pattern).
- Auditability: every consequential action links actor, scope, correlation
  IDs, before/after state.
- Age/safety gating where applicable, enforced deterministically before
  generation routing.
- Human override capability (`PRESERVED` dashboard review/approve/edit/
  reject + manual send paths) remains available in V2.
