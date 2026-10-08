/**
 * Owner-truth selectors for the Command Center owner experience.
 *
 * Pure functions only. Every join uses exact causal lineage:
 * - `mission_id` identity,
 * - `created_from` directive id (durable lineage, substring/prefix match),
 * - work-hierarchy `mission_to_objective` / `mission_to_campaign` links,
 * - decision-queue `mission_id` refs.
 *
 * Never borrows titles, verdicts, or next actions across missions. Never
 * guesses by recency, title substring, or truncated windows. `UNKNOWN` stays
 * a valid output wherever lineage is absent.
 */
import type {
  AutonomyProjection,
  Mission,
  OwnerAttentionRecord,
  OwnerControlSurface,
  WorkHierarchy,
} from "./contracts";

/** Standing owner goal / directive record (durable controls shape). */
export interface OwnerStandingGoal {
  directive_id: string;
  active?: unknown;
  status?: unknown;
  updated_at?: string | null;
  updatedAt?: string | null;
  command_type?: unknown;
  commandType?: unknown;
  label?: unknown;
  exact_text?: unknown;
  exactText?: unknown;
  [key: string]: unknown;
}

/**
 * UI mission row. Snapshot rows carry the durable `created_from` directive id
 * on `owner` (see snapshotMapper.mapMissions); test fixtures may carry it on
 * an explicit `created_from` field instead. Both are accepted; `created_from`
 * wins when present.
 */
export type OwnerMissionLike = Mission & {
  created_from?: string | null;
  createdFrom?: string | null;
  updated_at?: string | null;
  updatedAt?: string | null;
};

/** Decision-queue input: open-record list, `{ open }` page, or full surface. */
export type DecisionQueueInput =
  | readonly OwnerAttentionRecord[]
  | { open?: readonly OwnerAttentionRecord[] | null }
  | OwnerControlSurface
  | null
  | undefined;

const TERMINAL_HISTORY_STATES = new Set(["BLOCKED", "HALTED", "CANCELLED"]);

function asRecord(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object"
    ? (value as Record<string, unknown>)
    : {};
}

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function asList<T>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : [];
}

/** Durable directive id behind a mission: `created_from` first, then `owner`. */
export function missionDirectiveId(mission: unknown): string {
  const record = asRecord(mission);
  const created = asString(record.created_from ?? record.createdFrom).trim();
  if (created && created !== "UNKNOWN") return created;
  const owner = asString(record.owner).trim();
  if (owner && owner !== "UNKNOWN") return owner;
  return "";
}

/** Newest-first timestamp for a mission row (`updated_at`, else `startedAt`). */
export function missionUpdatedAt(mission: unknown): string {
  const record = asRecord(mission);
  return asString(
    record.updated_at ?? record.updatedAt ?? record.startedAt ?? record.started_at,
  );
}

function timeValue(iso: string): number {
  const parsed = Date.parse(iso);
  return Number.isFinite(parsed) ? parsed : 0;
}

function directiveIdOfGoal(goal: unknown): string {
  const record = asRecord(goal);
  return asString(record.directive_id ?? record.directiveId);
}

function goalUpdatedAt(goal: unknown): string {
  const record = asRecord(goal);
  return asString(record.updated_at ?? record.updatedAt);
}

function isActiveStandingGoal(goal: unknown): boolean {
  const record = asRecord(goal);
  if (record.active !== undefined && record.active !== null) {
    const active = record.active;
    return (
      active === 1 ||
      active === true ||
      active === "1" ||
      active === "true" ||
      (typeof active === "string" && active.toUpperCase() === "ACTIVE")
    );
  }
  const status = asString(record.status ?? record.state).toUpperCase();
  return status === "ACTIVE";
}

function goalShortLabel(goal: unknown): string {
  const record = asRecord(goal);
  const label = asString(record.label).trim();
  if (label && label !== "UNKNOWN") return label;
  const exact = asString(
    record.exact_text ?? record.exactText ?? record.text ?? record.objective,
  ).trim();
  if (exact && exact !== "UNKNOWN") return exact;
  return "UNKNOWN";
}

/**
 * `created_from` prefix/containment join: the mission lineage contains the
 * directive id. Exact equality satisfies this too.
 */
function lineageServesDirective(lineage: string, directiveId: string): boolean {
  return Boolean(lineage && directiveId && lineage.includes(directiveId));
}

/**
 * Rank standing owner goals per spec §2: active directives first
 * (active=1, any command_type including DELEGATE), each band by
 * `updated_at DESC`. Executing missions never reorder goals — children
 * resolve within the ranked goal via `currentChildForGoal` / `goalChildren`.
 * `missions`, `hierarchy`, and `autonomy` are accepted for signature
 * stability with later consumers and do not affect the order.
 */
export function rankOwnerGoals(
  goals: readonly OwnerStandingGoal[] | null | undefined,
  missions?: readonly OwnerMissionLike[] | null,
  hierarchy?: WorkHierarchy | null,
  autonomy?: AutonomyProjection | null,
): OwnerStandingGoal[] {
  void missions;
  void hierarchy;
  void autonomy;
  return asList<OwnerStandingGoal>(goals)
    .map((goal, index) => ({ goal, index }))
    .sort((a, b) => {
      const activeBand =
        (isActiveStandingGoal(a.goal) ? 0 : 1) - (isActiveStandingGoal(b.goal) ? 0 : 1);
      if (activeBand !== 0) return activeBand;
      const recency = timeValue(goalUpdatedAt(b.goal)) - timeValue(goalUpdatedAt(a.goal));
      if (recency !== 0) return recency;
      return a.index - b.index;
    })
    .map((entry) => entry.goal);
}

/** Mission ids bound to a directive through the work hierarchy. */
function hierarchyMissionIdsForDirective(
  hierarchy: WorkHierarchy,
  directiveId: string,
): Set<string> {
  const ids = new Set<string>();
  if (!directiveId) return ids;
  const root = asRecord(hierarchy);
  const hierarchyGoals = asList<Record<string, unknown>>(root.goals);
  const goalIds = new Set<string>();
  for (const raw of hierarchyGoals) {
    const record = asRecord(raw);
    const recordDirectiveId = asString(record.directive_id ?? record.directiveId);
    const recordGoalId = asString(record.goal_id ?? record.goalId);
    if (recordDirectiveId === directiveId || recordGoalId === directiveId) {
      if (recordGoalId) goalIds.add(recordGoalId);
    }
  }
  if (goalIds.size === 0) return ids;
  const objectiveIds = new Set<string>();
  for (const raw of asList<Record<string, unknown>>(root.objectives)) {
    const record = asRecord(raw);
    const goalId = asString(record.goal_id ?? record.goalId);
    const objectiveId = asString(record.objective_id ?? record.objectiveId);
    if (!goalId || !goalIds.has(goalId) || !objectiveId) continue;
    objectiveIds.add(objectiveId);
    for (const missionId of asList<unknown>(record.mission_ids ?? record.missionIds)) {
      const id = asString(missionId);
      if (id) ids.add(id);
    }
  }
  if (objectiveIds.size === 0) return ids;
  for (const raw of asList<Record<string, unknown>>(root.missions)) {
    const record = asRecord(raw);
    const missionId = asString(record.mission_id ?? record.missionId);
    const objectiveId = asString(record.objective_id ?? record.objectiveId);
    if (missionId && objectiveId && objectiveIds.has(objectiveId)) ids.add(missionId);
  }
  for (const raw of asList<Record<string, unknown>>(root.campaigns)) {
    const record = asRecord(raw);
    const objectiveId = asString(record.objective_id ?? record.objectiveId);
    if (!objectiveId || !objectiveIds.has(objectiveId)) continue;
    for (const missionId of asList<unknown>(record.mission_ids ?? record.missionIds)) {
      const id = asString(missionId);
      if (id) ids.add(id);
    }
  }
  return ids;
}

function byMissionRecency(a: OwnerMissionLike, b: OwnerMissionLike): number {
  return timeValue(missionUpdatedAt(b)) - timeValue(missionUpdatedAt(a));
}

/**
 * All child missions serving a goal directive, newest first: `created_from`
 * lineage matches plus work-hierarchy `mission_to_objective` /
 * `mission_to_campaign` links. Never matches by title.
 */
export function goalChildren(
  goalDirectiveId: string,
  missions: readonly OwnerMissionLike[] | null | undefined,
  hierarchy?: WorkHierarchy | null,
): OwnerMissionLike[] {
  if (!goalDirectiveId || !Array.isArray(missions)) return [];
  const hierarchyIds = hierarchy
    ? hierarchyMissionIdsForDirective(hierarchy, goalDirectiveId)
    : null;
  return missions
    .filter((mission): mission is OwnerMissionLike => {
      if (!mission || typeof mission !== "object") return false;
      const lineage = missionDirectiveId(mission);
      if (lineage && lineageServesDirective(lineage, goalDirectiveId)) return true;
      if (hierarchyIds && hierarchyIds.has(asString(asRecord(mission).id))) return true;
      return false;
    })
    .slice()
    .sort(byMissionRecency);
}

/**
 * Newest child mission for a goal directive (by `updated_at DESC` within the
 * `created_from` lineage). `null` when the directive has no children.
 */
export function currentChildForGoal(
  goalDirectiveId: string,
  missions: readonly OwnerMissionLike[] | null | undefined,
  hierarchy?: WorkHierarchy | null,
): OwnerMissionLike | null {
  const children = goalChildren(goalDirectiveId, missions, hierarchy);
  return children.length > 0 ? children[0] : null;
}

function lineageMarksHistory(lineage: string): boolean {
  const lowered = lineage.toLowerCase();
  return (
    lowered.includes("pw-dupkey") || lowered.includes("ci_failure") || lowered.includes("ci-failure")
  );
}

function activeGoalIdList(
  activeGoalIds: readonly string[] | ReadonlySet<string> | null | undefined,
): string[] {
  if (activeGoalIds instanceof Set) return [...activeGoalIds].map(String);
  if (Array.isArray(activeGoalIds)) return activeGoalIds.map(String);
  return [];
}

/**
 * Historical-mission check per spec §2: true when the mission is
 * BLOCKED/HALTED/CANCELLED and is not linked to any active goal via
 * `created_from` (or hierarchy, when provided), or when its `created_from`
 * carries a dup-key / CI_FAILURE marker. Recency alone never decides.
 * The decision queue does not decide history: an unlinked mission stays
 * historical even with an open record (attention surfaces separately via
 * `ownerActionForMission`).
 */
export function isHistoricalMission(
  mission: OwnerMissionLike | null | undefined,
  activeGoalIds: readonly string[] | ReadonlySet<string> | null | undefined,
  decisionQueue?: DecisionQueueInput,
  hierarchy?: WorkHierarchy | null,
): boolean {
  void decisionQueue;
  const record = asRecord(mission);
  if (!asString(record.id)) return false;
  if (!TERMINAL_HISTORY_STATES.has(asString(record.status).toUpperCase())) return false;
  const lineage = missionDirectiveId(mission);
  if (lineage && lineageMarksHistory(lineage)) return true;
  const goals = activeGoalIdList(activeGoalIds);
  if (lineage && goals.some((goalId) => lineageServesDirective(lineage, goalId))) {
    return false;
  }
  if (hierarchy) {
    const missionId = asString(record.id);
    for (const goalId of goals) {
      if (!goalId) continue;
      if (hierarchyMissionIdsForDirective(hierarchy, goalId).has(missionId)) return false;
    }
  }
  return true;
}

/**
 * Short parent-goal label a mission serves, for the Serves line and
 * goal-filtered Work links. `created_from` match first (most specific
 * directive id wins), then work-hierarchy links. `UNKNOWN` when unlinked —
 * never a borrowed label from outside the mission's subgraph.
 */
export function servingGoalLabel(
  mission: OwnerMissionLike | null | undefined,
  goals: readonly OwnerStandingGoal[] | null | undefined,
  hierarchy?: WorkHierarchy | null,
): string {
  const lineage = missionDirectiveId(mission);
  const goalList = asList<OwnerStandingGoal>(goals);
  if (lineage) {
    const matches = goalList
      .map((goal) => ({ goal, directiveId: directiveIdOfGoal(goal) }))
      .filter(
        (entry): entry is { goal: OwnerStandingGoal; directiveId: string } =>
          Boolean(entry.directiveId && lineageServesDirective(lineage, entry.directiveId)),
      )
      .sort((a, b) => b.directiveId.length - a.directiveId.length);
    if (matches.length > 0) return goalShortLabel(matches[0].goal);
  }
  if (hierarchy) {
    const missionId = asString(asRecord(mission).id);
    if (missionId) {
      const hierarchyGoals = asList<Record<string, unknown>>(asRecord(hierarchy).goals);
      for (const raw of hierarchyGoals) {
        const hierarchyGoal = asRecord(raw);
        const directiveId = asString(
          hierarchyGoal.directive_id ?? hierarchyGoal.directiveId,
        );
        const goalId = asString(hierarchyGoal.goal_id ?? hierarchyGoal.goalId);
        const linked =
          (directiveId &&
            hierarchyMissionIdsForDirective(hierarchy, directiveId).has(missionId)) ||
          (goalId && hierarchyMissionIdsForDirective(hierarchy, goalId).has(missionId));
        if (!linked) continue;
        if (directiveId) {
          const owned = goalList.find((goal) => directiveIdOfGoal(goal) === directiveId);
          if (owned) {
            const label = goalShortLabel(owned);
            if (label !== "UNKNOWN") return label;
          }
        }
        const hierarchyLabel = asString(hierarchyGoal.label).trim();
        if (hierarchyLabel && hierarchyLabel !== "UNKNOWN") return hierarchyLabel;
      }
    }
  }
  return "UNKNOWN";
}

function openDecisionRecords(queue: DecisionQueueInput): OwnerAttentionRecord[] {
  if (!queue) return [];
  if (Array.isArray(queue)) return queue as OwnerAttentionRecord[];
  const root = asRecord(queue);
  if (Array.isArray(root.open)) return root.open as OwnerAttentionRecord[];
  const nested = asRecord(root.decisionQueue);
  if (Array.isArray(nested.open)) return nested.open as OwnerAttentionRecord[];
  return [];
}

/**
 * Genuinely required owner action for a mission: the summary (else
 * consequence) of the OPEN decision-queue record referencing this
 * `mission_id`. `null` when no such record exists — the UI then renders
 * "Nothing needed from you."
 */
export function ownerActionForMission(
  mission: OwnerMissionLike | null | undefined,
  decisionQueue: DecisionQueueInput,
): string | null {
  const missionId = asString(asRecord(mission).id);
  if (!missionId) return null;
  for (const raw of openDecisionRecords(decisionQueue)) {
    const record = asRecord(raw);
    if (asString(record.status).toUpperCase() !== "OPEN") continue;
    if (asString(record.mission_id ?? record.missionId) !== missionId) continue;
    const summary = asString(record.summary).trim();
    if (summary) return summary;
    const consequence = asString(record.consequence).trim();
    if (consequence) return consequence;
    return "UNKNOWN";
  }
  return null;
}

/**
 * Verbatim leading sentences of an objective (no paraphrase). Defaults to 3.
 * `UNKNOWN` when there is no objective text.
 */
export function plainSummary(objective: unknown, maxSentences = 3): string {
  const text = typeof objective === "string" ? objective.trim() : "";
  if (!text) return "UNKNOWN";
  if (maxSentences < 1) return "";
  const sentences: string[] = [];
  const pattern = /[^.!?]+[.!?]+/g;
  let match: RegExpExecArray | null;
  let consumed = 0;
  while ((match = pattern.exec(text)) !== null) {
    const sentence = match[0].trim();
    if (sentence) sentences.push(sentence);
    consumed = pattern.lastIndex;
    if (sentences.length >= maxSentences) break;
  }
  if (sentences.length < maxSentences) {
    const tail = text.slice(consumed).trim();
    if (tail) sentences.push(tail);
  }
  return sentences.slice(0, maxSentences).join(" ");
}
