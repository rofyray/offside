"use client";
import type { Dispatch, SetStateAction } from "react";
import type { CheckState } from "../lib/reducer";
import { safeUrl } from "../lib/stream";
import { verdictClass, verdictName, type Claim } from "../lib/types";
import { WatchButton } from "./Watchlist";
import { LinkSimple } from "@phosphor-icons/react/dist/ssr/LinkSimple";
import { CheckCircle as Check } from "@phosphor-icons/react/dist/ssr/CheckCircle";
import { CaretLeft as ChevronLeft } from "@phosphor-icons/react/dist/ssr/CaretLeft";
import { CaretRight as ChevronRight } from "@phosphor-icons/react/dist/ssr/CaretRight";
import { CopySimple as Copy } from "@phosphor-icons/react/dist/ssr/CopySimple";
type ResultProps = {
  state: CheckState;
  answer: string;
  replaying: boolean;
  selected: number;
  setSelected: Dispatch<SetStateAction<number>>;
  tab: string;
  setTab: (tab: string) => void;
  onEdit: () => void;
  copy: (text: string) => Promise<void>;
};
function MarkedAnswer({
  answer,
  claims,
  select,
}: {
  answer: string;
  claims: Claim[];
  select: (n: number) => void;
}) {
  let cursor = 0;
  const parts: React.ReactNode[] = [];
  [...claims]
    .sort((a, b) => a.span[0] - b.span[0])
    .forEach((c) => {
      parts.push(answer.slice(cursor, c.span[0]));
      const index = claims.findIndex((x) => x.id === c.id);
      parts.push(
        <a
          href="#claim-review"
          key={c.id}
          className={`claim-mark ${verdictClass(c.verdict)}`}
          onClick={() => select(index)}
          title={`${verdictName(c.verdict)}: ${c.text}`}
        >
          {answer.slice(...c.span)}
          <sup>{index + 1}</sup>
        </a>,
      );
      cursor = c.span[1];
    });
  parts.push(answer.slice(cursor));
  return <p className="marked-answer">{parts}</p>;
}
function Line({
  claims,
  select,
}: {
  claims: Claim[];
  select: (n: number) => void;
}) {
  const dates = claims.flatMap((c) =>
    (c.sources || []).flatMap((s) =>
      s.published_date ? [Date.parse(s.published_date)] : [],
    ),
  );
  const today = Date.now(),
    start = dates.length
      ? Math.min(...dates, today - 86400000 * 30)
      : today - 86400000 * 365;
  const percent = (date: number) =>
    Math.max(0, Math.min(98, ((date - start) / (today - start)) * 98));
  return (
    <section className="line-panel">
      <div className="row between">
        <h2>The Line</h2>
        <p className="muted small">
          Solid dot: newest evidence. Undated evidence stays unplaced.
        </p>
      </div>
      <div className="scroll">
        <div className="line-grid">
          <div className="line-scale">
            <span>Claim</span>
            <span>
              {new Date(start).toLocaleDateString(undefined, {
                month: "short",
                year: "numeric",
              })}
              <b>Today</b>
            </span>
          </div>
          {claims.map((c, i) => {
            const dated = (c.sources || []).flatMap((s) =>
              s.published_date ? [Date.parse(s.published_date)] : [],
            );
            return (
              <a
                href="#claim-review"
                key={c.id}
                className={`line-row ${verdictClass(c.verdict)}`}
                onClick={() => select(i)}
              >
                <span>
                  <small>{String(i + 1).padStart(2, "0")}</small>
                  {c.text}
                </span>
                <span className="track">
                  {dated.length ? (
                    <i
                      className="evidence-dot"
                      style={{ left: `${percent(Math.max(...dated))}%` }}
                    />
                  ) : (
                    <em>
                      {c.verdict ? "No dated evidence" : "Finding sources"}
                    </em>
                  )}
                  <i className="today-line" />
                </span>
              </a>
            );
          })}
        </div>
      </div>
    </section>
  );
}
export default function CheckResultView({
  state,
  answer,
  replaying,
  selected,
  setSelected,
  tab,
  setTab,
  onEdit,
  copy,
}: ResultProps) {
  const running = state.status === "running";
  const claims = state.claims;
  const claim = claims[Math.min(selected, Math.max(0, claims.length - 1))];
  const scores = claims.flatMap((c) =>
    c.verdict?.freshness != null ? [c.verdict.freshness] : [],
  );
  const freshness = scores.length
    ? Math.round(scores.reduce((a, b) => a + b, 0) / scores.length)
    : null;
  const allSources = claims.flatMap((c) => c.sources || []);
  return (
    <>
      <section className="result-summary">
        <div>
          <div className="eyebrow">
            {running
              ? "Under review"
              : state.error
                ? "Review interrupted"
                : "Review complete"}
          </div>
          <p className="answer-preview">
            {answer.slice(0, 220)}
            {answer.length > 220 ? "…" : ""}
          </p>
          <button className="secondary" onClick={onEdit}>
            Edit Answer
          </button>
          {!running && !replaying && state.result && (
            <WatchButton
              key={state.result.run_id}
              runId={state.result.run_id}
            />
          )}
        </div>
        <div className="score">
          <strong>{freshness ?? "?"}</strong>
          <span>Freshness out of 100</span>
        </div>
        <dl className="counts">
          {[
            ["Offside", "out of date"],
            ["Tight call", "partly true"],
            ["Onside", "still current"],
            ["No call", "not settled"],
          ].map(([name, description]) => (
            <div className={name.toLowerCase().replace(" ", "-")} key={name}>
              <dt>
                {claims.filter((c) => verdictName(c.verdict) === name).length}
              </dt>
              <dd>
                {name} · {description}
              </dd>
            </div>
          ))}
        </dl>
      </section>
      {claims.length > 0 && (
        <>
          <Line claims={claims} select={setSelected} />
          <section className="result-grid">
            <article>
              <h2>The Answer, Marked Up</h2>
              <MarkedAnswer
                answer={answer}
                claims={claims}
                select={setSelected}
              />
              <div className="tabs" role="tablist" aria-label="Result views">
                {["Corrected Answer", "What Changed", "JSON"].map((t) => (
                  <button
                    role="tab"
                    aria-selected={t === tab}
                    key={t}
                    className={t === tab ? "active" : ""}
                    onClick={() => setTab(t)}
                  >
                    {t}
                  </button>
                ))}
              </div>
              <div className="result-content" role="tabpanel">
                {tab === "JSON" ? (
                  <pre>
                    {JSON.stringify(state.result || { claims }, null, 2)}
                  </pre>
                ) : tab === "What Changed" ? (
                  <ul className="changes">
                    {claims.map((c) => (
                      <li key={c.id}>
                        <b className={verdictClass(c.verdict)}>
                          {verdictName(c.verdict)}
                        </b>
                        <p>
                          {c.verdict?.current_value ||
                            c.verdict?.note ||
                            "Checking this claim…"}
                        </p>
                      </li>
                    ))}
                  </ul>
                ) : state.correction ? (
                  <>
                    <p>{state.correction.text}</p>
                    <ol className="citation-list">
                      {state.correction.citations.map((c) => {
                        const source = allSources.find(
                          (s) => s.id === c.source_id,
                        );
                        return source ? (
                          <li key={c.n}>
                            <a
                              href={safeUrl(source.url)}
                              target="_blank"
                              rel="noreferrer"
                            >
                              [{c.n}] {source.title}
                              <LinkSimple
                                weight="duotone"
                                aria-hidden="true"
                                size={12}
                              />
                            </a>
                          </li>
                        ) : null;
                      })}
                    </ol>
                    <div className="row">
                      <button
                        className="light"
                        onClick={() => copy(state.correction!.text)}
                      >
                        <Copy weight="duotone" aria-hidden="true" size={15} />
                        Copy Answer
                      </button>
                      <button
                        className="secondary"
                        onClick={() =>
                          copy(`${location.origin}/?run=${state.runId}`)
                        }
                        disabled={running}
                      >
                        Share Result
                      </button>
                    </div>
                  </>
                ) : (
                  <p className="muted">
                    The corrected answer will appear when the evidence is in.
                  </p>
                )}
              </div>
            </article>
            {claim && (
              <aside id="claim-review" className="review panel">
                <div className="row between">
                  <h2 className="eyebrow">
                    Review · claim {selected + 1} of {claims.length}
                  </h2>
                  <b className={`badge ${verdictClass(claim.verdict)}`}>
                    {verdictName(claim.verdict)}
                  </b>
                </div>
                <div className={`claim-score ${verdictClass(claim.verdict)}`}>
                  {claim.verdict?.freshness ?? "?"}
                </div>
                <dl>
                  <dt>The Answer Says</dt>
                  <dd>{claim.text}</dd>
                  <dt>The Evidence Says</dt>
                  <dd>
                    {claim.verdict?.current_value ||
                      claim.verdict?.note ||
                      "Checking current sources…"}
                  </dd>
                </dl>
                <h3>Evidence</h3>
                {(claim.sources || []).map((s) => (
                  <div className="source" key={s.id}>
                    <a href={safeUrl(s.url)} target="_blank" rel="noreferrer">
                      {s.domain}
                      <LinkSimple
                        weight="duotone"
                        aria-hidden="true"
                        size={12}
                      />
                    </a>
                    <small>
                      {s.published_date || "Undated"}
                      {s.official ? " · official" : ""}
                    </small>
                    <p>
                      {claim.verdict?.quotes.find((q) => q.source_id === s.id)
                        ?.text ||
                        s.snippet
                          .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
                          .replace(/[#*_`]/g, "")
                          .slice(0, 240)}
                    </p>
                  </div>
                ))}
                {claim.verdict && (
                  <p className="muted small">
                    Called by {claim.verdict.decided_by}
                    {claim.verdict.model
                      ? ` on ${claim.verdict.model} via Bifrost`
                      : ""}{" "}
                    · recheck by {claim.verdict.recheck_by || "a later date"}
                  </p>
                )}
                <div className="row">
                  <button
                    className="secondary"
                    aria-label="Previous claim"
                    disabled={selected === 0}
                    onClick={() => setSelected((n) => n - 1)}
                  >
                    <ChevronLeft
                      weight="duotone"
                      aria-hidden="true"
                      size={15}
                    />
                    Prev
                  </button>
                  <button
                    className="secondary"
                    aria-label="Next claim"
                    disabled={selected === claims.length - 1}
                    onClick={() => setSelected((n) => n + 1)}
                  >
                    Next
                    <ChevronRight
                      weight="duotone"
                      aria-hidden="true"
                      size={15}
                    />
                  </button>
                </div>
              </aside>
            )}
          </section>
        </>
      )}
      {!running && claims.length === 0 && !state.error && (
        <div className="panel">
          <Check weight="duotone" aria-hidden="true" size={22} />
          <h2>No Time-Sensitive Claims Found</h2>
          <p>There were no dated facts to score in this answer.</p>
        </div>
      )}
    </>
  );
}
