"""观测自 `ApprovalExecutionFact.__post_init__` 的金表：每条校验规则一个最小触发用例。
不要手改。要改就先读 §AJ：这张表按规则生成，重新生成的方法与"为什么不能按字段生成"都记在
`docs/compose/spec/backend-extensibility-inventory.md` §AJ。
"""

CASES = [
 {
  "case": "baseline-workspace_authorization_mode_change",
  "action": "workspace_authorization_mode_change",
  "over": {},
  "msg": None
 },
 {
  "case": "baseline-dataset_workspace_transfer",
  "action": "dataset_workspace_transfer",
  "over": {},
  "msg": None
 },
 {
  "case": "baseline-knowledge_base_release_publish",
  "action": "knowledge_base_release_publish",
  "over": {},
  "msg": None
 },
 {
  "case": "baseline-knowledge_base_release_rollback",
  "action": "knowledge_base_release_rollback",
  "over": {},
  "msg": None
 },
 {
  "case": "baseline-knowledge_base_release_quality_waiver",
  "action": "knowledge_base_release_quality_waiver",
  "over": {},
  "msg": None
 },
 {
  "case": "baseline-document_purge",
  "action": "document_purge",
  "over": {},
  "msg": None
 },
 {
  "case": "req-tenant_id",
  "action": "document_purge",
  "over": {
   "tenant_id": None
  },
  "msg": "tenant_id is required for ApprovalExecutionFact"
 },
 {
  "case": "req-approval_request_id",
  "action": "document_purge",
  "over": {
   "approval_request_id": None
  },
  "msg": "approval_request_id is required for ApprovalExecutionFact"
 },
 {
  "case": "req-execution_id",
  "action": "document_purge",
  "over": {
   "execution_id": None
  },
  "msg": "execution_id is required for ApprovalExecutionFact"
 },
 {
  "case": "req-resource_type",
  "action": "document_purge",
  "over": {
   "resource_type": None
  },
  "msg": "resource_type is required for ApprovalExecutionFact"
 },
 {
  "case": "req-resource_id",
  "action": "document_purge",
  "over": {
   "resource_id": None
  },
  "msg": "resource_id is required for ApprovalExecutionFact"
 },
 {
  "case": "req-snapshot_hash",
  "action": "document_purge",
  "over": {
   "snapshot_hash": None
  },
  "msg": "snapshot_hash is required for ApprovalExecutionFact"
 },
 {
  "case": "req-reason",
  "action": "document_purge",
  "over": {
   "reason": None
  },
  "msg": "reason is required for ApprovalExecutionFact"
 },
 {
  "case": "req-action_type",
  "action": "document_purge",
  "over": {
   "action_type": None
  },
  "msg": "action_type is required for ApprovalExecutionFact"
 },
 {
  "case": "posint-request_revision",
  "action": "document_purge",
  "over": {
   "request_revision": 0
  },
  "msg": "request_revision must be a positive integer"
 },
 {
  "case": "posint-execution_revision",
  "action": "document_purge",
  "over": {
   "execution_revision": 0
  },
  "msg": "execution_revision must be a positive integer"
 },
 {
  "case": "revision-follow",
  "action": "document_purge",
  "over": {
   "execution_revision": 5
  },
  "msg": "execution_revision must immediately follow request_revision"
 },
 {
  "case": "snapshot-hash-hex",
  "action": "document_purge",
  "over": {
   "snapshot_hash": "zz"
  },
  "msg": "snapshot_hash must be a SHA-256 hex digest"
 },
 {
  "case": "wsauth-req-workspace_revision",
  "action": "workspace_authorization_mode_change",
  "over": {
   "workspace_revision": None
  },
  "msg": "workspace_revision is required for Workspace authorization fact"
 },
 {
  "case": "wsauth-req-from_mode",
  "action": "workspace_authorization_mode_change",
  "over": {
   "from_mode": None
  },
  "msg": "from_mode is required for Workspace authorization fact"
 },
 {
  "case": "wsauth-req-permission_matrix_fingerprint",
  "action": "workspace_authorization_mode_change",
  "over": {
   "permission_matrix_fingerprint": None
  },
  "msg": "permission_matrix_fingerprint is required for Workspace authorization fact"
 },
 {
  "case": "dswt-req-source_workspace_id",
  "action": "dataset_workspace_transfer",
  "over": {
   "source_workspace_id": None
  },
  "msg": "source_workspace_id is required for Dataset Workspace transfer fact"
 },
 {
  "case": "dswt-req-profile_revision",
  "action": "dataset_workspace_transfer",
  "over": {
   "profile_revision": None
  },
  "msg": "profile_revision is required for Dataset Workspace transfer fact"
 },
 {
  "case": "dswt-alias-dataset_profile",
  "action": "dataset_workspace_transfer",
  "over": {
   "profile_revision": 5
  },
  "msg": "Dataset profile revision aliases must agree"
 },
 {
  "case": "dswt-alias-ownership",
  "action": "dataset_workspace_transfer",
  "over": {
   "ownership_revision": 4
  },
  "msg": "Ownership revision aliases must agree"
 },
 {
  "case": "dswt-source-equals-target",
  "action": "dataset_workspace_transfer",
  "over": {
   "target_workspace_id": "w1"
  },
  "msg": "source and target Workspace must differ"
 },
 {
  "case": "dswt-alias-workspace_revision",
  "action": "dataset_workspace_transfer",
  "over": {
   "source_workspace_revision": 99
  },
  "msg": "Workspace revision aliases must agree"
 },
 {
  "case": "kbrel-req-release_number",
  "action": "knowledge_base_release_publish",
  "over": {
   "release_number": None
  },
  "msg": "release_number is required for Knowledge Base Release fact"
 },
 {
  "case": "kbrel-req-channel_revision",
  "action": "knowledge_base_release_publish",
  "over": {
   "channel_revision": None
  },
  "msg": "channel_revision is required for Knowledge Base Release fact"
 },
 {
  "case": "kbrel-req-mutation_generation",
  "action": "knowledge_base_release_publish",
  "over": {
   "mutation_generation": None
  },
  "msg": "mutation_generation is required for Knowledge Base Release fact"
 },
 {
  "case": "kbrel-manifest-lower",
  "action": "knowledge_base_release_publish",
  "over": {
   "manifest_digest": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
  },
  "msg": "manifest_digest must be a lowercase SHA-256 digest"
 },
 {
  "case": "waiver-req-policy_id",
  "action": "knowledge_base_release_quality_waiver",
  "over": {
   "policy_id": None
  },
  "msg": "policy_id is required for Quality Waiver fact"
 },
 {
  "case": "waiver-req-quality_gate_digest",
  "action": "knowledge_base_release_quality_waiver",
  "over": {
   "quality_gate_digest": None
  },
  "msg": "quality_gate_digest is required for Quality Waiver fact"
 },
 {
  "case": "waiver-policy-lower",
  "action": "knowledge_base_release_quality_waiver",
  "over": {
   "policy_digest": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
  },
  "msg": "policy_digest must be a lowercase SHA-256 digest"
 },
 {
  "case": "waiver-gate-digest-lower",
  "action": "knowledge_base_release_quality_waiver",
  "over": {
   "quality_gate_digest": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
  },
  "msg": "quality_gate_digest must be a lowercase SHA-256 digest"
 },
 {
  "case": "waiver-gate-revision-match",
  "action": "knowledge_base_release_quality_waiver",
  "over": {
   "quality_gate_revision": 14
  },
  "msg": "quality_gate_revision must match channel_revision"
 },
 {
  "case": "waiver-evidence-alias",
  "action": "knowledge_base_release_quality_waiver",
  "over": {
   "evidence_digest": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  },
  "msg": "quality evidence digest aliases must agree"
 },
 {
  "case": "waiver-evidence-lower",
  "action": "knowledge_base_release_quality_waiver",
  "over": {
   "quality_evidence_digest": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
   "evidence_digest": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
  },
  "msg": "quality_evidence_digest must be a lowercase SHA-256 digest"
 },
 {
  "case": "waiver-expiry-alias",
  "action": "knowledge_base_release_quality_waiver",
  "over": {
   "waiver_expires_at": "2031-01-01T00:00:00.000000Z"
  },
  "msg": "waiver expiry aliases must agree"
 },
 {
  "case": "waiver-expiry-not-iso",
  "action": "knowledge_base_release_quality_waiver",
  "over": {
   "waiver_expires_at": "not-a-date",
   "requested_expires_at": "not-a-date"
  },
  "msg": "waiver_expires_at must be an ISO datetime"
 },
 {
  "case": "waiver-expiry-non-canonical",
  "action": "knowledge_base_release_quality_waiver",
  "over": {
   "waiver_expires_at": "2030-01-01T00:00:00+00:00",
   "requested_expires_at": "2030-01-01T00:00:00+00:00"
  },
  "msg": "waiver_expires_at must be canonical"
 },
 {
  "case": "waiver-gate-state",
  "action": "knowledge_base_release_quality_waiver",
  "over": {
   "quality_gate_state": "passing"
  },
  "msg": "quality_gate_state must be blocked for Quality Waiver fact"
 },
 {
  "case": "waiver-gate-reason",
  "action": "knowledge_base_release_quality_waiver",
  "over": {
   "quality_gate_reason": ""
  },
  "msg": "quality_gate_reason is invalid"
 },
 {
  "case": "prec-dswt-missing-id-and-alias",
  "action": "dataset_workspace_transfer",
  "over": {
   "source_workspace_id": None,
   "profile_revision": 5
  },
  "msg": "Dataset profile revision aliases must agree"
 },
 {
  "case": "prec-dswt-two-aliases",
  "action": "dataset_workspace_transfer",
  "over": {
   "profile_revision": 5,
   "ownership_revision": 4
  },
  "msg": "Dataset profile revision aliases must agree"
 },
 {
  "case": "prec-dswt-same-target-and-ws-alias",
  "action": "dataset_workspace_transfer",
  "over": {
   "target_workspace_id": "w1",
   "source_workspace_revision": 99
  },
  "msg": "source and target Workspace must differ"
 },
 {
  "case": "prec-waiver-two-digests",
  "action": "knowledge_base_release_quality_waiver",
  "over": {
   "policy_digest": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
   "quality_gate_digest": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
  },
  "msg": "policy_digest must be a lowercase SHA-256 digest"
 },
 {
  "case": "prec-waiver-gate-rev-and-evidence",
  "action": "knowledge_base_release_quality_waiver",
  "over": {
   "quality_gate_revision": 14,
   "evidence_digest": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  },
  "msg": "quality_gate_revision must match channel_revision"
 },
 {
  "case": "prec-waiver-noncanonical-and-state",
  "action": "knowledge_base_release_quality_waiver",
  "over": {
   "waiver_expires_at": "2030-01-01T00:00:00+00:00",
   "requested_expires_at": "2030-01-01T00:00:00+00:00",
   "quality_gate_state": "passing"
  },
  "msg": "waiver_expires_at must be canonical"
 },
 {
  "case": "prec-kbrel-missing-int-and-upper-digest",
  "action": "knowledge_base_release_publish",
  "over": {
   "release_number": None,
   "manifest_digest": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
  },
  "msg": "release_number is required for Knowledge Base Release fact"
 },
 {
  "case": "prec-wsauth-missing-int-and-str",
  "action": "workspace_authorization_mode_change",
  "over": {
   "workspace_revision": None,
   "from_mode": None
  },
  "msg": "workspace_revision is required for Workspace authorization fact"
 },
 {
  "case": "prec-common-blank-reason-and-bad-rev",
  "action": "document_purge",
  "over": {
   "reason": "",
   "request_revision": 0
  },
  "msg": "reason is required for ApprovalExecutionFact"
 }
]

UNREACHABLE = ["waiver_expires_at is required for Quality Waiver fact"]
