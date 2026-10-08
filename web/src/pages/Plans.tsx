import { api } from "../api";
import { PlanCard } from "../components/PlanCard";
import { useLoad } from "../useLoad";

export function Plans() {
  const { data, error, reload } = useLoad(api.plans);
  if (error) return <p className="error">{error}</p>;
  if (!data) return <p className="hint">Loading…</p>;
  return (
    <section>
      <h2>Upcoming plans</h2>
      {data.plans.length === 0 && (
        <p className="hint">No plans yet. Forward a plan to the bot and tap Save.</p>
      )}
      {data.plans.map((p) => (
        <PlanCard key={`${p.id}:${p.version}`} plan={p} onChange={reload} />
      ))}
    </section>
  );
}
