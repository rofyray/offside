import type {
  Claim,
  CheckResult,
  Correction,
  StreamEvent,
  Telemetry,
  Verdict,
  Source,
} from "./types";
export type CheckState = {
  runId?: string;
  claims: Claim[];
  correction?: Correction;
  result?: CheckResult;
  telemetry?: Telemetry;
  error?: string;
  status: string;
};
export const initial: CheckState = { claims: [], status: "idle" };
export function reduceCheck(state: CheckState, event: StreamEvent): CheckState {
  const d = event.data;
  switch (event.type) {
    case "reset":
      return { ...initial, status: "running" };
    case "run.started":
      return { ...state, runId: d.run_id as string, status: "running" };
    case "claims.extracted":
      return {
        ...state,
        claims: (d.claims as Claim[]).map((c) => ({
          ...c,
          ...state.claims.find((old) => old.id === c.id),
        })),
      };
    case "evidence.ready":
      return {
        ...state,
        claims: state.claims.map((c) =>
          c.id === d.claim_id ? { ...c, sources: d.sources as Source[] } : c,
        ),
      };
    case "claim.verdict":
    case "claim.overturned":
      return {
        ...state,
        claims: state.claims.map((c) =>
          c.id === d.claim_id ? { ...c, verdict: d.verdict as Verdict } : c,
        ),
      };
    case "corrected.ready":
      return {
        ...state,
        correction: {
          text: d.text as string,
          citations: d.citations as Correction["citations"],
        },
      };
    case "run.telemetry":
      return { ...state, telemetry: d.telemetry as Telemetry };
    case "run.completed": {
      const result = d.result as CheckResult | undefined;
      return {
        ...state,
        status: "complete",
        ...(result
          ? {
              result,
              claims: result.claims,
              correction: result.corrected,
              telemetry: result.telemetry,
              runId: result.run_id,
            }
          : {}),
      };
    }
    case "run.error":
      return { ...state, status: "error", error: d.message as string };
    default:
      return state;
  }
}
