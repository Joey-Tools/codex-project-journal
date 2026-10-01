---
id: 20260918-71b420
title: Review Gate v2 Handoff
status: completed
created: 2026-09-18
updated: 2026-10-01
branch: codex/organization-v2-handoff
pr:
supersedes: []
superseded_by:
---

# Review Gate v2 Handoff

## Summary
- 已安装 canonical v2 verifier、controller，并以 `@JoeyTeng` CODEOWNERS 保护控制面。
- 组织收尾回执核验通过后，已移除临时 v1 bridge。

## Current State
- PR 工作流继续通过 `JoeyTeng/codex-review-gate-action@v2` 产生 `codex/github-review-gate` check。
- Verifier 工作流显式授予 `actions: read`，保留其余现有权限与运行行为。
- controller 仅处理新建评论；v2 工作流的请求者权限策略为 `any`。
- 设置 `CODEX_REVIEW_GATE_AUTO_REQUEST=true` 后，首次失败的 verifier run 可为唯一关联 PR 发起新审查，并将该 run 的 `head_sha` 作为预期 head。
- 临时 bridge 已删除，不再由本仓库产生 `codex/review-gate` legacy status。

## Evidence
- Canonical producer: `JoeyTeng/codex-review-gate-action@v2`.
- Source handoff implementation: https://github.com/Joey-Tools/codex-review-gate/pull/51.
- Post-cutover audit receipt SHA-256: `9a8b38f2188a14168423a07639d6662c87e198fe2dd12041f67fc224f363817e`.
