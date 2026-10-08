import { useState } from "react";
import { ApiError, api } from "../api";
import type { Rule } from "../types";
import { useLoad } from "../useLoad";

export function Blocks() {
  const { data, error, reload } = useLoad(api.blocks);
  const [text, setText] = useState("");
  const [preview, setPreview] = useState<Rule | null>(null);
  const [message, setMessage] = useState("");

  async function submit(confirm: boolean) {
    setMessage("");
    try {
      const result = await api.block(text, confirm);
      if (result.saved) {
        setText("");
        setPreview(null);
        reload();
      } else {
        // The server resolves the date; review it before saving.
        setPreview(result.block);
      }
    } catch (e) {
      setPreview(null);
      setMessage(e instanceof ApiError && typeof e.detail === "string" ? e.detail : "That didn't work. Try again.");
    }
  }

  return (
    <section>
      <h2>Busy blocks</h2>
      <p className="hint">Jobs, family and anything not in a timetable. Try “busy sat 2-6pm” or “work every Wed 6-10pm”.</p>
      <form
        className="stack"
        onSubmit={(e) => {
          e.preventDefault();
          submit(false);
        }}
      >
        <input
          aria-label="Busy block"
          placeholder="busy sat 2-6pm"
          value={text}
          maxLength={200}
          required
          onChange={(e) => {
            setText(e.target.value);
            setPreview(null);
          }}
        />
        {!preview && <button type="submit" className="on">Check</button>}
      </form>
      {preview && (
        <div className="card">
          <p className="pre">{preview.description}</p>
          <div className="row">
            <button className="on" onClick={() => submit(true)}>Save block</button>
            <button onClick={() => setPreview(null)}>Change</button>
          </div>
        </div>
      )}
      {message && <p className="error">{message}</p>}
      {error && <p className="error">{error}</p>}
      {data?.blocks.length === 0 && <p className="hint">No busy blocks yet.</p>}
      {data?.blocks.map((rule) => (
        <div key={rule.id} className="card">
          <p className="pre">{rule.description}</p>
          <button className="danger" onClick={() => api.removeBlock(rule.id).then(reload)}>Remove</button>
        </div>
      ))}
    </section>
  );
}
