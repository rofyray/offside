"use client";
import { useToast } from "./components/Toast";
import { replayExample, type Example } from "./lib/replay";
import Examples from "./components/Examples";
import Watchlist from "./components/Watchlist";
import Link from "next/link";
import CheckResultView from "./components/CheckResultView";
import { useEffect, useReducer, useRef, useState } from "react";
import { ArrowRight } from "@phosphor-icons/react/dist/ssr/ArrowRight";
import { PlusCircle as Plus } from "@phosphor-icons/react/dist/ssr/PlusCircle";
import { Broadcast as Radio } from "@phosphor-icons/react/dist/ssr/Broadcast";
import { Stop as Square } from "@phosphor-icons/react/dist/ssr/Stop";
import { TerminalWindow as Terminal } from "@phosphor-icons/react/dist/ssr/TerminalWindow";
import { ClockClockwise } from "@phosphor-icons/react/dist/ssr/ClockClockwise";
import { BookOpen } from "@phosphor-icons/react/dist/ssr/BookOpen";
import Hood from "./components/Hood";
import { initial, reduceCheck } from "./lib/reducer";
import { postEvents } from "./lib/stream";
import {
  type CheckResult,
  type StreamEvent,
  type Telemetry,
} from "./lib/types";

const prompts = [
  {
    label: "Pricing",
    text: "GitHub Copilot is only available on paid plans. Individual developers pay $10 per month, and there is no free tier.",
  },
  {
    label: "API Version",
    text: "Python 3.9 is the latest stable release of Python. Python 2.7 still receives security updates from the Python Software Foundation.",
  },
  {
    label: "Leadership",
    text: "Microsoft is led by CEO Steve Ballmer. Satya Nadella runs the company’s cloud and enterprise division.",
  },
];
const models = ["claude-haiku-4-5", "openai/gpt-6-luna", "claude-sonnet-5-5"];
function Logo() {
  return (
    <Link href="/" className="logo" aria-label="OffSide home">
      <svg width="20" height="30" viewBox="0 0 20 30" aria-hidden="true">
        <path d="M10 0v30" stroke="var(--yellow)" strokeWidth="2" />
        <circle cx="4" cy="22" r="3" fill="currentColor" />
        <circle cx="16" cy="8" r="3" fill="none" stroke="currentColor" />
      </svg>
      OffSide
    </Link>
  );
}
export default function Home() {
  const [view, setView] = useState("check");
  const [answer, setAnswer] = useState("");
  const [question, setQuestion] = useState("");
  const [live, setLive] = useState(true);
  const [official, setOfficial] = useState(true);
  const [state, dispatch] = useReducer(reduceCheck, initial);
  const [selected, setSelected] = useState(0);
  const [tab, setTab] = useState("Corrected Answer");
  const [editing, setEditing] = useState(true);
  const notify = useToast();
  const [replaying, setReplaying] = useState(false);
  const [chosen, setChosen] = useState(models);
  const [modelErrors, setModelErrors] = useState<Record<string, string>>({});
  const [modelAnswers, setModelAnswers] = useState<Record<string, string>>({});
  const [modelResults, setModelResults] = useState<Record<string, CheckResult>>(
    {},
  );
  const [leaderboard, setLeaderboard] = useState<
    { model: string; freshness: number | null; stale: number; total: number }[]
  >([]);
  const abort = useRef<AbortController | null>(null);
  useEffect(() => () => abort.current?.abort(), []);
  useEffect(() => {
    const id = new URLSearchParams(window.location.search).get("run");
    if (!id) return;
    fetch(`/api/py/runs/${encodeURIComponent(id)}`)
      .then(async (r) => {
        if (!r.ok)
          throw new Error("This result has expired or could not be found.");
        return r.json();
      })
      .then((result: CheckResult) => {
        setAnswer(result.answer);
        dispatch({ type: "run.completed", data: { result } });
        setEditing(false);
      })
      .catch((e) =>
        notify({
          tone: "error",
          title: "Couldn’t open result",
          message: e.message,
        }),
      );
  }, [notify]);
  const running = state.status === "running";
  const claims = state.claims;
  const telemetry: Telemetry | undefined = state.telemetry;
  async function replay(example: Example) {
    abort.current?.abort();
    const controller = new AbortController();
    abort.current = controller;
    dispatch({ type: "reset", data: {} });
    setReplaying(true);
    setEditing(false);
    notify({
      tone: "info",
      title: "Playing recorded check",
      message: `Recorded ${new Date(example.recorded_at).toLocaleDateString()}. No live searches are running.`,
    });
    setView("check");
    setSelected(0);
    setAnswer(String(example.input.answer));
    await replayExample(example, dispatch, controller.signal);
  }
  function reset() {
    setReplaying(false);
    abort.current?.abort();
    dispatch({ type: "reset", data: {} });
    dispatch({ type: "run.completed", data: {} });
    setEditing(true);
    setSelected(0);
  }
  async function start() {
    setReplaying(false);
    abort.current?.abort();
    const controller = new AbortController();
    abort.current = controller;
    dispatch({ type: "reset", data: {} });
    setSelected(0);
    setEditing(false);
    setModelAnswers({});
    setModelErrors({});
    setModelResults({});
    setLeaderboard([]);
    try {
      await postEvents(
        `/api/py/${view === "ask" ? "ask" : "check"}`,
        view === "ask"
          ? { question, models: chosen }
          : {
              answer,
              question: question || undefined,
              live,
              prefer_official: official,
            },
        (event: StreamEvent) => {
          if (event.type === "model.error")
            setModelErrors((old) => ({
              ...old,
              [event.data.model as string]: event.data.message as string,
            }));
          if (event.type === "model.answer")
            setModelAnswers((old) => ({
              ...old,
              [event.data.model as string]: event.data.text as string,
            }));
          if (event.type === "model.result")
            setModelResults((old) => ({
              ...old,
              [event.data.model as string]: event.data.result as CheckResult,
            }));
          if (event.type === "leaderboard.ready")
            setLeaderboard(event.data.rows as typeof leaderboard);
          if (!event.data.model) dispatch(event);
        },
        controller.signal,
      );
    } catch (error) {
      if (!controller.signal.aborted)
        dispatch({
          type: "run.error",
          data: {
            message:
              error instanceof Error
                ? error.message
                : "The check could not finish. Please try again.",
          },
        });
    }
  }
  async function copy(text: string) {
    if (replaying && text.includes("?run=")) {
      notify({
        tone: "warning",
        title: "Recorded example",
        message: "Run a fresh check to create a saved link.",
      });
      return;
    }
    try {
      await navigator.clipboard.writeText(text);
      notify({ tone: "success", title: "Copied to clipboard" });
    } catch {
      notify({
        tone: "error",
        title: "Couldn’t copy text",
        message: "Select the text to copy it.",
      });
    }
  }
  return (
    <>
      <header className="header">
        <div className="row header-left">
          <Logo />
          <nav aria-label="Primary">
            {[
              ["check", "Check Answer"],
              ["ask", "Ask Models"],
              ["watchlist", "Watchlist"],
              ["api", "API"],
            ].map(([id, name]) => (
              <button
                key={id}
                className={view === id ? "active" : ""}
                onClick={() => {
                  reset();
                  setView(id);
                }}
              >
                {name}
              </button>
            ))}
          </nav>
        </div>
        <button
          className="primary"
          onClick={() => {
            reset();
            setView("check");
          }}
        >
          <Plus weight="duotone" aria-hidden="true" size={17} />
          New Check
        </button>
      </header>
      <main>
        {replaying && (
          <p className="recorded-notice">
            Recorded Example · Replays without live searches.
          </p>
        )}
        {view === "watchlist" ? (
          <Watchlist />
        ) : view === "api" ? (
          <section className="api-panel">
            <div className="eyebrow">
              <Terminal weight="duotone" aria-hidden="true" size={16} />
              For Developers
            </div>
            <h1>
              A second opinion.
              <br />
              One API call.
            </h1>
            <p>
              Send an answer. Get claim-level verdicts, current sources, and a
              cited correction.
            </p>
            <pre>{`POST /api/py/verify\nContent-Type: application/json\n\n{\n  "answer": "The answer you want to check...",\n  "prefer_official": true,\n  "live": true\n}`}</pre>
            <a href="/api/py/docs" target="_blank" rel="noreferrer">
              API Reference{" "}
              <BookOpen weight="duotone" aria-hidden="true" size={15} />
            </a>
            <p className="muted">
              6 live runs per 10 minutes. 40 per day. Results remain available
              for 7 days.
            </p>
          </section>
        ) : (
          <>
            {(editing || view === "ask") && (
              <section className="input-section">
                <div className="intro">
                  <div className="eyebrow">
                    <ClockClockwise
                      size={18}
                      weight="duotone"
                      aria-hidden="true"
                    />
                    A freshness check for AI answers
                  </div>
                  <h1>
                    {view === "ask" ? (
                      <>
                        Same question.
                        <br />
                        <em>Who’s still onside?</em>
                      </>
                    ) : (
                      <>
                        Sounds right.
                        <br />
                        <em>Still true?</em>
                      </>
                    )}
                  </h1>
                  <p>
                    {view === "ask"
                      ? "Ask up to three models. Compare their answers against current sources."
                      : "Paste an AI answer. We check its time-sensitive claims against current sources and show you what changed."}
                  </p>
                  <div className="process">
                    <span>01 Extract claims</span>
                    <span>02 Check sources</span>
                    <span>03 Correct the answer</span>
                  </div>
                </div>
                <form
                  className="input-card"
                  onSubmit={(e) => {
                    e.preventDefault();
                    void start();
                  }}
                >
                  <div className="row between">
                    <label htmlFor="answer">
                      {view === "ask" ? "Your Question" : "Answer to Check"}
                    </label>
                    <span className="mono muted">
                      {view === "ask" ? "" : "20 to 6,000 characters"}
                    </span>
                  </div>
                  <textarea
                    id="answer"
                    required
                    minLength={view === "ask" ? 5 : 20}
                    maxLength={view === "ask" ? 2000 : 6000}
                    value={view === "ask" ? question : answer}
                    onChange={(e) =>
                      view === "ask"
                        ? setQuestion(e.target.value)
                        : setAnswer(e.target.value)
                    }
                    placeholder={
                      view === "ask"
                        ? "What are the current options for…"
                        : "Paste an answer here. Pricing, releases, people, policies. Anything that might have moved on."
                    }
                  />
                  {view === "check" ? (
                    <>
                      <details>
                        <summary>Add Context</summary>
                        <input
                          aria-label="Original question"
                          value={question}
                          onChange={(e) => setQuestion(e.target.value)}
                          maxLength={1000}
                          placeholder="What did you ask?"
                        />
                      </details>
                      <div className="toggles">
                        <label>
                          <input
                            type="checkbox"
                            checked={live}
                            onChange={(e) => setLive(e.target.checked)}
                          />
                          Fetch Live
                        </label>
                        <label>
                          <input
                            type="checkbox"
                            checked={official}
                            onChange={(e) => setOfficial(e.target.checked)}
                          />
                          Prefer Official Sources
                        </label>
                      </div>
                    </>
                  ) : (
                    <fieldset className="model-picker">
                      <legend>Choose Your Models</legend>
                      {models.map((m) => (
                        <label key={m}>
                          <input
                            type="checkbox"
                            checked={chosen.includes(m)}
                            onChange={(e) =>
                              setChosen((old) =>
                                e.target.checked
                                  ? [...old, m]
                                  : old.filter((x) => x !== m),
                              )
                            }
                          />
                          {m}
                        </label>
                      ))}
                    </fieldset>
                  )}
                  <button
                    className="primary run-button"
                    type="submit"
                    disabled={running || (view === "ask" && !chosen.length)}
                  >
                    {running
                      ? "Researching"
                      : view === "ask"
                        ? "Compare Models"
                        : "Check Answer"}
                    <ArrowRight weight="duotone" aria-hidden="true" size={18} />
                  </button>
                  <p className="input-note">
                    Live web research by Tavily. Model routing by Bifrost.
                  </p>
                </form>
              </section>
            )}
            {editing && view === "check" && (
              <section className="examples">
                <Examples onSelect={replay} />
                <span className="eyebrow">Try a Fresh Check</span>
                {prompts.map((p) => (
                  <button
                    key={p.label}
                    className="example"
                    onClick={() => {
                      setAnswer(p.text);
                      document.getElementById("answer")?.focus();
                    }}
                  >
                    <span>{p.label}</span>
                    <ArrowRight weight="duotone" aria-hidden="true" size={16} />
                  </button>
                ))}
                <small className="muted">
                  Sample inputs run a fresh check.
                </small>
              </section>
            )}
            {running && (
              <div className="research-status" role="status">
                <Radio weight="duotone" aria-hidden="true" size={17} />
                {claims.length
                  ? `${claims.filter((c) => c.verdict).length} of ${claims.length} claims checked`
                  : "Stepover is reading the answer and planning searches"}
                <button
                  onClick={() => {
                    abort.current?.abort();
                    dispatch({
                      type: "run.error",
                      data: {
                        message:
                          "Research stopped. Start another check when you are ready.",
                      },
                    });
                  }}
                >
                  <Square weight="duotone" aria-hidden="true" size={13} />
                  Stop
                </button>
              </div>
            )}
            {state.error && (
              <div className="error" role="alert">
                {state.error}
                <button onClick={() => setEditing(true)}>Try Again</button>
              </div>
            )}
            {view === "ask" && (
              <section className="model-results">
                {Object.entries(modelErrors).map(([model, message]) => (
                  <div className="error" role="alert" key={model}>
                    {model}: {message}
                  </div>
                ))}
                {Object.entries(modelAnswers).map(([m, text]) => (
                  <article className="panel" key={m}>
                    <div className="eyebrow">{m}</div>
                    <p>{text}</p>
                    {modelResults[m] ? (
                      <strong className="accent">
                        Freshness {modelResults[m].freshness ?? "Not settled"} ·{" "}
                        {modelResults[m].counts.offside} offside
                      </strong>
                    ) : (
                      <span className="muted">Checking claims…</span>
                    )}
                  </article>
                ))}
                {leaderboard.length > 0 && (
                  <div className="panel">
                    <h2>Freshness Leaderboard</h2>
                    {leaderboard.map((r, i) => (
                      <div className="leader-row" key={r.model}>
                        <b>{i + 1}</b>
                        <span>{r.model}</span>
                        <strong>{r.freshness ?? "No call"}</strong>
                        <span>
                          {r.stale} of {r.total} offside
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </section>
            )}
            {!editing && view === "check" && (
              <CheckResultView
                state={state}
                answer={answer}
                replaying={replaying}
                selected={selected}
                setSelected={setSelected}
                tab={tab}
                setTab={setTab}
                onEdit={() => setEditing(true)}
                copy={copy}
              />
            )}
          </>
        )}
      </main>
      <footer>
        <span className="mono">Tavily × Bifrost</span>
        <span>
          {telemetry
            ? `${(telemetry.total_ms / 1000).toFixed(1)} s · ${telemetry.tavily.credits} Tavily credits`
            : "Every call has a source. Every verdict has a reason."}
        </span>
        <Hood telemetry={telemetry} />
      </footer>
    </>
  );
}
