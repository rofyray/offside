"use client";
import { useEffect, useState } from "react";
import { Binoculars } from "@phosphor-icons/react/dist/ssr/Binoculars";
import { ClockClockwise } from "@phosphor-icons/react/dist/ssr/ClockClockwise";
import { Flag } from "@phosphor-icons/react/dist/ssr/Flag";
import { ArrowRight } from "@phosphor-icons/react/dist/ssr/ArrowRight";
import { CheckCircle } from "@phosphor-icons/react/dist/ssr/CheckCircle";
import { useToast } from "./Toast";

type Watch = {
  id: string;
  run_id: string;
  latest_run_id: string;
  answer: string;
  freshness: number | null;
  source_count: number;
  status: string;
  message: string;
  expires_at: number;
  next_scan_at: number;
  last_scan_at: number | null;
  events: {
    at: number;
    kind: string;
    run_id: string;
    freshness: number | null;
    changes: { text: string; from: string; to: string }[];
  }[];
};
async function watchRequest(path = "", options?: RequestInit) {
  const response = await fetch(`/api/py/watchlist${path}`, options);
  const data = await response.json();
  if (!response.ok)
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : "The watchlist could not be updated.",
    );
  return data;
}
const date = (seconds: number) =>
  new Date(seconds * 1000).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
const labels: Record<string, string> = {
  starting: "Starting",
  watching: "Watching",
  partial: "Partial Scan",
  retrying: "Retrying",
  budget_paused: "Budget Paused",
};
const verdicts: Record<string, string> = {
  current: "Onside",
  partially_current: "Tight Call",
  outdated: "Offside",
  contradicted: "Offside",
  unverifiable: "No Call",
};

export function WatchButton({ runId }: { runId: string }) {
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  const notify = useToast();
  useEffect(() => {
    const controller = new AbortController();
    setSaved(false);
    watchRequest("", { signal: controller.signal })
      .then((data) =>
        setSaved(
          data.watches.some(
            (w: Watch) => w.run_id === runId || w.latest_run_id === runId,
          ),
        ),
      )
      .catch(() => {});
    return () => controller.abort();
  }, [runId]);
  async function save() {
    setBusy(true);
    try {
      await watchRequest("", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ run_id: runId }),
      });
      setSaved(true);
      notify({
        tone: "success",
        title: "Watching for 7 days",
        message: "See source changes and new verdicts in Watchlist.",
      });
    } catch (error) {
      notify({
        tone: "error",
        title: "Couldn’t save watch",
        message: error instanceof Error ? error.message : "Please try again.",
      });
    } finally {
      setBusy(false);
    }
  }
  const Icon = saved ? CheckCircle : Binoculars;
  return (
    <button className="secondary" onClick={save} disabled={busy || saved}>
      <Icon size={17} weight="duotone" aria-hidden="true" />
      {saved ? "Watching" : busy ? "Saving" : "Watch Answer"}
    </button>
  );
}

export default function Watchlist() {
  const notify = useToast();
  const [watches, setWatches] = useState<Watch[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [available, setAvailable] = useState(true);
  const [stopping, setStopping] = useState<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    async function refresh() {
      try {
        const data = await watchRequest("", { signal: controller.signal });
        setWatches(data.watches);
        setAvailable(data.available);
        setError("");
      } catch (err) {
        if (!controller.signal.aborted)
          setError(
            err instanceof Error ? err.message : "Could not load your watches.",
          );
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }
    void refresh();
    const timer = setInterval(refresh, 30000);
    return () => {
      clearInterval(timer);
      controller.abort();
    };
  }, []);
  async function stop(id: string) {
    setStopping(id);
    setError("");
    try {
      await watchRequest(`/${id}`, { method: "DELETE" });
      setWatches((items) => items.filter((w) => w.id !== id));
      notify({ tone: "success", title: "Watch stopped" });
    } catch (err) {
      notify({
        tone: "error",
        title: "Couldn’t stop watch",
        message: err instanceof Error ? err.message : "Please try again.",
      });
    } finally {
      setStopping(null);
    }
  }
  return (
    <section className="watchlist">
      <div className="eyebrow">
        <Binoculars size={19} weight="duotone" aria-hidden="true" />
        Keep an Eye on It
      </div>
      <h1>
        Still true.
        <br />
        <em>Or time to rethink?</em>
      </h1>
      <p className="watch-intro">
        Watch up to five checked answers for seven days. Pathway scans their
        cited sources every six hours and rechecks answers when the content
        changes.
      </p>
      <div className="watch-meta">
        <span>
          <ClockClockwise size={17} weight="duotone" aria-hidden="true" />{" "}
          Six-Hour Scans
        </span>
        <span>Seven-Day Watches</span>
        <span>{watches.length} / 5 Saved</span>
      </div>
      <p className="small muted">
        Saved in this browser. Clearing cookies loses access. Monitoring shares
        a daily research budget; paused scans resume the next day.
      </p>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {loading ? (
        <p role="status">Loading Watches…</p>
      ) : !available ? (
        <div className="watch-empty panel">
          <h2>Monitoring Unavailable</h2>
          <p>
            Source monitoring is not configured yet. You can still run a fresh
            answer check.
          </p>
        </div>
      ) : !watches.length ? (
        <div className="watch-empty panel">
          <Binoculars size={38} weight="duotone" aria-hidden="true" />
          <h2>Your Watchlist Is Clear</h2>
          <p>
            Check an answer, then select <strong>Watch Answer</strong> on its
            result. Source changes and new verdicts will appear here.
          </p>
        </div>
      ) : (
        <div className="watch-grid">
          {watches.map((w) => (
            <article className="watch-card panel" key={w.id}>
              <div className="row between">
                <span className="eyebrow">
                  {labels[w.status] || "Watching"}
                </span>
                <span className="watch-score">
                  {w.freshness ?? "—"}
                  <small> / 100</small>
                </span>
              </div>
              <h2 className="watch-answer">{w.answer}</h2>
              <p className="small muted">{w.message}</p>
              <dl className="watch-dates">
                <div>
                  <dt>Sources</dt>
                  <dd>{w.source_count} monitored</dd>
                </div>
                <div>
                  <dt>Last Scan</dt>
                  <dd>{w.last_scan_at ? date(w.last_scan_at) : "Pending"}</dd>
                </div>
                <div>
                  <dt>Next Scan</dt>
                  <dd>{date(w.next_scan_at)}</dd>
                </div>
                <div>
                  <dt>Ends</dt>
                  <dd>{date(w.expires_at)}</dd>
                </div>
              </dl>
              {w.events.length > 0 && (
                <ol className="watch-events">
                  {w.events.map((event, i) => (
                    <li key={`${event.run_id}-${i}`}>
                      <Flag
                        size={18}
                        weight="duotone"
                        aria-hidden="true"
                        className={
                          event.kind === "went_offside" ? "watch-alert" : ""
                        }
                      />
                      <div>
                        <a href={`/?run=${encodeURIComponent(event.run_id)}`}>
                          {event.kind === "went_offside"
                            ? "Went Offside"
                            : "Answer Rechecked"}
                        </a>
                        <time
                          dateTime={new Date(event.at * 1000).toISOString()}
                        >
                          {date(event.at)}
                        </time>
                        {event.changes.map((change, j) => (
                          <p key={j}>
                            {change.text}{" "}
                            <span className="muted">
                              {verdicts[change.from]} → {verdicts[change.to]}
                            </span>
                          </p>
                        ))}
                      </div>
                    </li>
                  ))}
                </ol>
              )}
              <div className="row between watch-controls">
                <a href={`/?run=${encodeURIComponent(w.latest_run_id)}`}>
                  Open Result{" "}
                  <ArrowRight size={16} weight="duotone" aria-hidden="true" />
                </a>
                <button
                  className="secondary"
                  onClick={() => stop(w.id)}
                  disabled={stopping === w.id}
                >
                  {stopping === w.id ? "Stopping" : "Stop Watch"}
                </button>
              </div>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
