import { runArtifactCommand } from './runtime-artifacts.js'
import { runCaptureReviewCommand } from './runtime-capture-review.js'
import { runDecisionCommand } from './runtime-decisions.js'
import { DOMAIN_HELP, runDomainCommand } from './runtime-domains.js'
import { EXECUTION_HELP, runExecutionCommand } from './runtime-execution.js'
import type { RuntimeFeature } from './runtime-feature-session.js'
import { COMMITMENT_HELP, CORRESPONDENCE_HELP, runCommitmentCommand, runCorrespondenceCommand } from './runtime-followthrough.js'
import { runApprovalCommand, runDeliveryCommand, runMissionCommand } from './runtime-missions.js'
import { runCaptureCommand, runMemoryCommand, runProjectCommand } from './runtime-projects.js'
import { RESEARCH_HELP, runResearchCommand } from './runtime-research.js'
import { runSpecialistCommand, SPECIALIST_HELP } from './runtime-specialists.js'
import { runStatusCommand } from './runtime-status.js'
import { runTemplateCommand } from './runtime-templates.js'
import { runScheduleCommand, runWorkflowCommand, SCHEDULE_HELP, WORKFLOW_HELP } from './runtime-workflows.js'

/** Concrete inspector consumers only; Python generated contracts remain the wire authority. */
export const runtimeFeatures: Record<string, RuntimeFeature> = {
  decision: { label: 'Recorded decisions', help: 'inspect [cursor]; read-only receipt explanations, no activation', run: runDecisionCommand },
  overview: { label: 'Current work & repair', help: 'queues [project] | effect/reconcile <effect-id> | privacy', run: runStatusCommand },
  specialist: { label: 'Specialists & channels', help: SPECIALIST_HELP, run: runSpecialistCommand },
  execution: { label: 'Reviewed execution', help: EXECUTION_HELP, run: runExecutionCommand },
  commitment: { label: 'Reviewed commitments', help: COMMITMENT_HELP, run: runCommitmentCommand },
  correspondence: { label: 'Draft correspondence', help: CORRESPONDENCE_HELP, run: runCorrespondenceCommand },
  domain: { label: 'Data & creative packages', help: DOMAIN_HELP, run: runDomainCommand },
  workflow: { label: 'Manual workflows', help: WORKFLOW_HELP, run: runWorkflowCommand },
  schedule: { label: 'Monitor health', help: SCHEDULE_HELP, run: runScheduleCommand },
  research: { label: 'Research & briefs', help: RESEARCH_HELP, run: runResearchCommand },
  mission: { label: 'Mission progress', help: 'get/list | create <id> <outcome> | revise <revision> <outcome> | attach <revision> <output> <project> <artifact> <version> | verify/accept/pause/resume/cancel <revision> | receipts', run: runMissionCommand },
  approval: { label: 'Pending decisions', help: 'list | deny <approval-id> <digest>; approve only in exact-content review', run: runApprovalCommand },
  delivery: { label: 'Delivery recovery', help: 'status/retry <delivery-id>; never reruns a mission', run: runDeliveryCommand },
  artifact: { label: 'Artifact versions', help: 'inspect/compare <project> <artifact> <version(s)> | prepare/edit <exact JSON> | publish <command> <approval> <digest> | status/cancel <command>', run: runArtifactCommand },
  template: { label: 'Reusable templates', help: 'list <project> | get <template> [version] | create <reviewed structure JSON>', run: runTemplateCommand },
  project: { label: 'Projects & resume', help: 'list | create <name> | get/select/resume <project-id> | attach <project-id> <revision> <artifact-id> <version>', run: runProjectCommand },
  memory: { label: 'Scoped memory', help: 'list [project-id] | get <record-id> | remember <scope> <preference> | correct <record-id> <version> <scope> <preference> | forget <record-id> <version>', run: runMemoryCommand },
  capture: { label: 'Capture inbox', help: 'list <project-id> | get <capture-id> | add <project-id> <artifact-id> <version> <annotation> | file <capture-id> <project-id|-> <revision> | duplicates <project-id> | inspect <capture-id> | search <project-id> <query> | process <capture-id> <expected-extraction-sequence> <original|latest_extraction>', run: (argument, request, sessionId) => ['inspect', 'search', 'process'].includes(argument.trim().split(/\s+/)[0]) ? runCaptureReviewCommand(argument, request, sessionId) : runCaptureCommand(argument, request, sessionId) }
}
