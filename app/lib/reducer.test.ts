import { it, expect } from "vitest";
import { initial, reduceCheck } from "./reducer";
const claim = {
  id: "c1",
  text: "test",
  span: [0, 4],
  category: "other",
  volatility_days: 120,
  stated_value: "",
  queries: [],
  preferred_domains: [],
};
it("applies an overturned verdict without losing evidence", () => {
  let state = reduceCheck(initial, {
    type: "claims.extracted",
    data: { claims: [claim] },
  });
  state = reduceCheck(state, {
    type: "evidence.ready",
    data: { claim_id: "c1", sources: [{ id: "s1" }] },
  });
  state = reduceCheck(state, {
    type: "claim.verdict",
    data: { claim_id: "c1", verdict: { label: "current" } },
  });
  state = reduceCheck(state, {
    type: "claim.overturned",
    data: { claim_id: "c1", verdict: { label: "outdated" } },
  });
  expect(state.claims[0].verdict?.label).toBe("outdated");
  expect(state.claims[0].sources?.[0].id).toBe("s1");
});
it("clears previous data for a new run", () => {
  expect(
    reduceCheck(
      { ...initial, error: "old", claims: [] },
      { type: "reset", data: {} },
    ),
  ).toEqual({ ...initial, status: "running" });
});
it("records a visible failure", () =>
  expect(
    reduceCheck(initial, { type: "run.error", data: { message: "Try again" } })
      .error,
  ).toBe("Try again"));
