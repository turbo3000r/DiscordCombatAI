import { useEffect, useMemo, useState } from "react";
import {
  getSuggestion,
  listSuggestions,
  respond,
  resetIdempotency,
  type Suggestion,
} from "./api";
import { getRuntime } from "./runtime";

type PageState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "unauthorized" }
  | { kind: "forbidden" }
  | { kind: "empty" }
  | { kind: "list"; items: Suggestion[] }
  | { kind: "detail"; item: Suggestion; notice?: string };

export function App() {
  const runtime = getRuntime();
  const [state, setState] = useState<PageState>({ kind: "loading" });
  const [responseText, setResponseText] = useState("");
  const [busy, setBusy] = useState(false);
  const [statusFilter, setStatusFilter] = useState("all");

  async function loadList() {
    setState({ kind: "loading" });
    try {
      const items = await listSuggestions();
      if (!items.length) setState({ kind: "empty" });
      else setState({ kind: "list", items });
    } catch (err) {
      const message = err instanceof Error ? err.message : "error";
      if (message === "unauthorized") setState({ kind: "unauthorized" });
      else if (message === "forbidden") setState({ kind: "forbidden" });
      else setState({ kind: "error", message });
    }
  }

  useEffect(() => {
    void loadList();
  }, []);

  const visibleItems = useMemo(() => {
    if (state.kind !== "list") return [];
    if (statusFilter === "all") return state.items;
    return state.items.filter((item) => item.status === statusFilter);
  }, [state, statusFilter]);

  async function openDetail(id: string) {
    setBusy(true);
    try {
      const item = await getSuggestion(id);
      setResponseText(item.response_text || "");
      setState({ kind: "detail", item });
    } catch (err) {
      const message = err instanceof Error ? err.message : "error";
      if (message === "unauthorized") setState({ kind: "unauthorized" });
      else if (message === "forbidden") setState({ kind: "forbidden" });
      else setState({ kind: "error", message });
    } finally {
      setBusy(false);
    }
  }

  async function onRespond(mode: string) {
    if (state.kind !== "detail") return;
    setBusy(true);
    try {
      const result = await respond(state.item.id, mode, responseText);
      const notice =
        result.status === 202
          ? "Saved; notification pending delivery."
          : result.body.notification_status === "pending" ||
              result.body.notification_status === "claiming"
            ? "Saved; notification in progress."
            : "Saved.";
      setState({ kind: "detail", item: result.body, notice });
    } catch (err) {
      const message = err instanceof Error ? err.message : "error";
      if (message === "conflict") {
        const item = await getSuggestion(state.item.id);
        setState({ kind: "detail", item, notice: "Conflict — reloaded." });
        resetIdempotency();
      } else if (message === "unauthorized") setState({ kind: "unauthorized" });
      else if (message === "forbidden") setState({ kind: "forbidden" });
      else setState({ kind: "error", message });
    } finally {
      setBusy(false);
    }
  }

  const pending = state.kind === "detail" && state.item.status === "pending";
  const deliveryInProgress =
    state.kind === "detail" &&
    (state.item.notification_status === "pending" ||
      state.item.notification_status === "claiming");
  const failedRetry =
    state.kind === "detail" &&
    state.item.status === "done" &&
    state.item.notification_status === "failed";
  const formEnabled = ((pending && !deliveryInProgress) || failedRetry) && !busy;

  return (
    <div className="app">
      {runtime.mode === "development" && (
        <div className="banner">DEVELOPMENT — local admin mode</div>
      )}
      <header>
        <h1>Suggestions</h1>
        <nav>
          <button type="button" onClick={() => void loadList()} disabled={busy}>
            List
          </button>
        </nav>
      </header>
      <main>
        {state.kind === "loading" && <p>Loading…</p>}
        {state.kind === "empty" && <p>No suggestions.</p>}
        {state.kind === "error" && <p className="error">{state.message}</p>}
        {state.kind === "unauthorized" && <p className="error">Please sign in.</p>}
        {state.kind === "forbidden" && (
          <p className="error">Not authorized for admin access.</p>
        )}
        {state.kind === "list" && (
          <section>
            <label>
              Status
              <select
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value)}
              >
                <option value="all">All</option>
                <option value="pending">Pending</option>
                <option value="done">Done</option>
              </select>
            </label>
            {!visibleItems.length ? (
              <p>No suggestions match filters.</p>
            ) : (
              <ul className="list">
                {visibleItems.map((item) => (
                  <li key={item.id}>
                    <button type="button" onClick={() => void openDetail(item.id)}>
                      <strong>{item.ticket_uid}</strong> — {item.title}{" "}
                      <span className="badge">{item.status}</span>
                      {item.notification_status && (
                        <span className="badge">{item.notification_status}</span>
                      )}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>
        )}
        {state.kind === "detail" && (
          <section className="detail">
            <h2>{state.item.ticket_uid}</h2>
            <p>{state.item.title}</p>
            <p>{state.item.details}</p>
            {deliveryInProgress && (
              <p className="notice">Notification delivery in progress.</p>
            )}
            {state.notice && <p className="notice">{state.notice}</p>}
            {(state.item.conversation || []).length > 0 && (
              <div className="conversation">
                <h3>Conversation</h3>
                <ul>
                  {(state.item.conversation || []).map((entry, index) => (
                    <li key={`${entry.role || "entry"}-${index}`}>
                      <strong>{entry.role || "note"}:</strong>{" "}
                      {entry.text || entry.content || ""}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            <label>
              Response
              <textarea
                value={responseText}
                disabled={!formEnabled}
                onChange={(e) => {
                  resetIdempotency();
                  setResponseText(e.target.value);
                }}
              />
            </label>
            <div className="actions">
              <button
                type="button"
                disabled={!formEnabled}
                onClick={() => void onRespond("send")}
              >
                Send
              </button>
              <button
                type="button"
                disabled={!pending || deliveryInProgress || busy}
                onClick={() => void onRespond("done_auto_feedback")}
              >
                Done + auto feedback
              </button>
              <button
                type="button"
                disabled={!pending || deliveryInProgress || busy}
                onClick={() => void onRespond("done_no_feedback")}
              >
                Done (no feedback)
              </button>
            </div>
          </section>
        )}
      </main>
    </div>
  );
}
