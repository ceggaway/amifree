import { useState } from "react";
import { ApiError, api } from "../api";
import { day, fromInput, span, toInput } from "../format";
import type { Plan, Status } from "../types";
import { ClashBanner } from "./ClashBanner";

const LABELS: Record<string, string> = {
  SAVED: "Saved, not decided",
  GOING: "Going",
  MAYBE: "Maybe",
  DECLINED: "Can't make it",
  CANCELLED: "Cancelled",
  PREVIEW: "Not saved",
};

export function PlanCard({ plan, onChange, compact }: { plan: Plan; onChange?: () => void; compact?: boolean }) {
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState("");
  const [form, setForm] = useState({
    title: plan.title,
    start_at: toInput(plan.start_at),
    end_at: toInput(plan.end_at),
    location: plan.location ?? "",
  });

  async function patch(body: Record<string, unknown>) {
    setError("");
    try {
      await api.patchPlan(plan.id, { version: plan.version, ...body });
      setEditing(false);
      onChange?.();
    } catch (e) {
      const detail = e instanceof ApiError ? (e.detail as { reason?: string } | string) : null;
      if (typeof detail === "object" && detail?.reason === "clash") {
        // Same rule as the bot's "Going anyway": a clash must be acknowledged.
        if (window.confirm("This clashes with another commitment. Go anyway?")) {
          await patch({ ...body, force: true });
        }
      } else if (typeof detail === "object" && detail?.reason === "stale") {
        setError("This plan changed elsewhere. Showing the latest version.");
        onChange?.();
      } else {
        setError(typeof detail === "string" ? detail : "That didn't save. Try again.");
      }
    }
  }

  const choose = (status: Status) => () => patch({ status });

  return (
    <article className="card">
      <h3>{plan.title}</h3>
      <p>
        {day(plan.start_at)} · {span(plan.start_at, plan.end_at)}
        {plan.location ? ` · ${plan.location}` : ""}
      </p>
      <ClashBanner availability={plan.availability} />
      <p className="hint">
        {LABELS[plan.status]}
        {plan.shared ? " · group plan" : ""}
      </p>
      {!compact && onChange && !editing && (
        <div className="row">
          <button className={plan.status === "GOING" ? "on" : ""} onClick={choose("GOING")}>Going</button>
          <button className={plan.status === "MAYBE" ? "on" : ""} onClick={choose("MAYBE")}>Maybe</button>
          <button className={plan.status === "DECLINED" ? "on" : ""} onClick={choose("DECLINED")}>Can't</button>
          {/* Group plans are edited once by the organiser, from the card in the group. */}
          {!plan.shared && <button onClick={() => setEditing(true)}>Edit</button>}
        </div>
      )}
      {editing && (
        <form
          className="stack"
          onSubmit={(e) => {
            e.preventDefault();
            patch({
              title: form.title,
              start_at: fromInput(form.start_at),
              end_at: fromInput(form.end_at),
              location: form.location || null,
            });
          }}
        >
          <input aria-label="Title" value={form.title} maxLength={200} required onChange={(e) => setForm({ ...form, title: e.target.value })} />
          <input aria-label="Starts" type="datetime-local" value={form.start_at} required onChange={(e) => setForm({ ...form, start_at: e.target.value })} />
          <input aria-label="Ends" type="datetime-local" value={form.end_at} required onChange={(e) => setForm({ ...form, end_at: e.target.value })} />
          <input aria-label="Venue" placeholder="Venue" value={form.location} maxLength={300} onChange={(e) => setForm({ ...form, location: e.target.value })} />
          <div className="row">
            <button type="submit" className="on">Save changes</button>
            <button type="button" onClick={() => setEditing(false)}>Keep unchanged</button>
            <button
              type="button"
              className="danger"
              onClick={() => window.confirm("Cancel this plan?") && patch({ cancel: true })}
            >
              Cancel plan
            </button>
          </div>
        </form>
      )}
      {error && <p className="error">{error}</p>}
    </article>
  );
}
