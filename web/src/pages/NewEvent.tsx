import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, refusal } from "../api";
import { ClashBanner } from "../components/ClashBanner";
import { at, shiftKey, todayKey } from "../format";
import { haptic } from "../telegram";
import { forget } from "../useLoad";
import type { Availability } from "../types";

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

/** Python's weekday (0 = Monday) for a date key, in Singapore. */
const weekday = (key: string) => (new Date(`${key}T12:00:00+08:00`).getUTCDay() + 6) % 7;

/** Make an event card for the group: the same card /event posts, from a form. */
export function NewEvent() {
  const { code = "" } = useParams();
  const navigate = useNavigate();
  // One id per form: a double tap or a retry posts one card, not two.
  const [requestId] = useState(() => crypto.randomUUID());
  const [form, setForm] = useState({
    title: "",
    date: shiftKey(todayKey(), 1),
    start: "19:00",
    end: "21:00",
    location: "",
    notes: "",
    repeat: false,
    weekdays: [] as number[],
    until: shiftKey(todayKey(), 56),
  });
  const [mine, setMine] = useState<Availability | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const set = (change: Partial<typeof form>) => setForm((f) => ({ ...f, ...change }));

  const start = at(form.date, form.start);
  // An end before the start runs past midnight into the next day.
  const end = form.end > form.start ? at(form.date, form.end) : at(shiftKey(form.date, 1), form.end);

  // Your own clash check for the chosen time, as the card's Am I free? would show it.
  useEffect(() => {
    if (!form.date || !form.start || !form.end) return;
    const timer = setTimeout(() => {
      api.availability(new Date(start), new Date(end)).then(setMine).catch(() => setMine(null));
    }, 400);
    return () => clearTimeout(timer);
  }, [start, end, form.date, form.start, form.end]);

  function toggleDay(index: number) {
    set({ weekdays: form.weekdays.includes(index) ? form.weekdays.filter((d) => d !== index) : [...form.weekdays, index] });
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setBusy(true);
    const weekdays = form.repeat ? (form.weekdays.length ? form.weekdays : [weekday(form.date)]) : [];
    try {
      const made = await api.createEvent(code, {
        request_id: requestId,
        title: form.title.trim(),
        start_at: start,
        end_at: end,
        location: form.location.trim() || null,
        notes: form.notes.trim() || null,
        weekdays,
        until: form.repeat ? form.until : null,
      });
      haptic("success");
      forget(`group:${code}`);
      navigate(`/e/${made.id}?posted=1`, { replace: true });
    } catch (err) {
      haptic("error");
      setError(refusal(err).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="stack" onSubmit={submit}>
      <h1>Create an event</h1>
      <label>
        What
        <input value={form.title} maxLength={200} required placeholder="Dinner" onChange={(e) => set({ title: e.target.value })} />
      </label>
      <label>
        Date
        <input type="date" value={form.date} min={todayKey()} required onChange={(e) => set({ date: e.target.value })} />
      </label>
      <div className="pair">
        <label>
          Starts
          <input type="time" value={form.start} required onChange={(e) => set({ start: e.target.value })} />
        </label>
        <label>
          Ends
          <input type="time" value={form.end} required onChange={(e) => set({ end: e.target.value })} />
        </label>
      </div>
      <label>
        <span>
          Where <span className="hint">(optional)</span>
        </span>
        <input value={form.location} maxLength={300} placeholder="Vivo" onChange={(e) => set({ location: e.target.value })} />
      </label>
      <label>
        <span>
          Notes <span className="hint">(optional)</span>
        </span>
        <textarea value={form.notes} maxLength={1000} rows={2} onChange={(e) => set({ notes: e.target.value })} />
      </label>
      <label className="check">
        <input type="checkbox" checked={form.repeat} onChange={(e) => set({ repeat: e.target.checked })} /> Repeats every week
      </label>
      {form.repeat && (
        <div className="stack indent">
          <div className="row" role="group" aria-label="Repeats on">
            {DAYS.map((name, index) => (
              <button
                key={name}
                type="button"
                className={`chip-button${(form.weekdays.length ? form.weekdays : [weekday(form.date)]).includes(index) ? " on" : ""}`}
                aria-pressed={(form.weekdays.length ? form.weekdays : [weekday(form.date)]).includes(index)}
                onClick={() => toggleDay(index)}
              >
                {name}
              </button>
            ))}
          </div>
          <label>
            Until
            <input type="date" value={form.until} min={form.date} required onChange={(e) => set({ until: e.target.value })} />
          </label>
          <p className="hint">Each week gets its own card.</p>
        </div>
      )}
      {mine && (
        <section>
          <ClashBanner availability={mine} />
        </section>
      )}
      {error && <p className="error">{error}</p>}
      <button type="submit" className="on big" disabled={busy}>
        {busy ? "Posting…" : "Post to the group"}
      </button>
    </form>
  );
}
