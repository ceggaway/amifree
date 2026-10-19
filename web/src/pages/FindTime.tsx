import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, refusal } from "../api";
import { shiftKey, todayKey } from "../format";
import { haptic } from "../telegram";

const LENGTHS = [
  [30, "30 min"],
  [60, "1h"],
  [90, "1.5h"],
  [120, "2h"],
  [180, "3h"],
] as const;

/** Monday (as a date key) of the week holding `key`. */
const monday = (key: string) => shiftKey(key, -((new Date(`${key}T12:00:00+08:00`).getUTCDay() + 6) % 7));

function window(choice: string): [string, string] {
  const today = todayKey();
  const week = monday(today);
  if (choice === "this week") return [today, shiftKey(week, 6)];
  if (choice === "next week") return [shiftKey(week, 7), shiftKey(week, 13)];
  if (choice === "weekend") {
    // This Sat–Sun; on a Sunday, just today. (Date keys compare correctly as strings.)
    const saturday = shiftKey(week, 5);
    return [saturday < today ? today : saturday, shiftKey(week, 6)];
  }
  return [shiftKey(today, 1), shiftKey(today, 7)];
}

/** The /whosfree poll from a form: members add their times, the best slots are suggested. */
export function FindTime() {
  const { code = "" } = useParams();
  const navigate = useNavigate();
  const [requestId] = useState(() => crypto.randomUUID());
  const [choice, setChoice] = useState("next 7 days");
  const [[first, last], setRange] = useState(window("next 7 days"));
  const [title, setTitle] = useState("");
  const [minutes, setMinutes] = useState(60);
  const [replyBy, setReplyBy] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  function pick(next: string) {
    setChoice(next);
    if (next !== "custom") setRange(window(next));
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      const made = await api.createPoll(code, {
        request_id: requestId,
        title: title.trim(),
        minutes,
        first,
        last,
        reply_by: replyBy || null,
      });
      haptic("success");
      // The organiser's own times come first, so the poll starts with one answer.
      navigate(`/poll/${made.id}/mine`, { replace: true });
    } catch (err) {
      haptic("error");
      setError(refusal(err).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="stack" onSubmit={submit}>
      <h1>Find a time</h1>
      <p className="hint">
        Members add the times they're free, and AmIFree suggests the best slots. The group only ever
        sees how many people are free, never who is busy.
      </p>
      <label>
        What for
        <input value={title} maxLength={200} required placeholder="Project meeting" onChange={(e) => setTitle(e.target.value)} />
      </label>
      <fieldset>
        <legend>How long</legend>
        <div className="row">
          {LENGTHS.map(([value, label]) => (
            <button key={value} type="button" className={`chip-button${minutes === value ? " on" : ""}`} aria-pressed={minutes === value} onClick={() => setMinutes(value)}>
              {label}
            </button>
          ))}
        </div>
      </fieldset>
      <fieldset>
        <legend>When</legend>
        <div className="row">
          {["next 7 days", "this week", "next week", "weekend", "custom"].map((name) => (
            <button key={name} type="button" className={`chip-button${choice === name ? " on" : ""}`} aria-pressed={choice === name} onClick={() => pick(name)}>
              {name[0].toUpperCase() + name.slice(1)}
            </button>
          ))}
        </div>
        {choice === "custom" && (
          <div className="pair">
            <label>
              From
              <input type="date" value={first} min={todayKey()} required onChange={(e) => setRange([e.target.value, last < e.target.value ? e.target.value : last])} />
            </label>
            <label>
              To
              <input type="date" value={last} min={first} max={shiftKey(first, 13)} required onChange={(e) => setRange([first, e.target.value])} />
            </label>
          </div>
        )}
      </fieldset>
      <label>
        <span>
          Reply by <span className="hint">(optional, otherwise halfway)</span>
        </span>
        <input type="date" value={replyBy} min={todayKey()} max={last} onChange={(e) => setReplyBy(e.target.value)} />
      </label>
      {error && <p className="error">{error}</p>}
      <button type="submit" className="on big" disabled={busy}>
        {busy ? "Posting…" : "Post the poll to the group"}
      </button>
    </form>
  );
}
