const work = [
  ["Research", "Checking Spain SMB opportunities", "Working"],
  ["Create", "Preparing DINERO GRATIS carousel", "Queued"],
  ["QA", "Fact + character + disclosure checks", "Queued"],
];

export default function Home() {
  return (
    <main style={{ maxWidth: 1120, margin: "0 auto", padding: 24 }}>
      <section style={{ background: "white", borderRadius: 24, padding: 24, border: "1px solid #e6e6e0" }}>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 20, alignItems: "center" }}>
          <div>
            <div style={{ fontSize: 13, opacity: 0.6 }}>AI CREATOR · SPAIN</div>
            <h1 style={{ margin: "6px 0" }}>Sofía</h1>
            <div>Mission: become the most useful AI creator for Spanish SMB owners.</div>
          </div>
          <div style={{ padding: "10px 14px", borderRadius: 999, background: "#eef7ee" }}>● Working</div>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0, 1fr))", gap: 12, marginTop: 24 }}>
          {[["Opportunities", "18"], ["Creating", "4"], ["DMs", "—"], ["Audits", "—"]].map(([k, v]) => (
            <div key={k} style={{ padding: 16, border: "1px solid #ecece7", borderRadius: 16 }}>
              <div style={{ opacity: 0.55, fontSize: 13 }}>{k}</div>
              <div style={{ fontSize: 28, fontWeight: 650, marginTop: 4 }}>{v}</div>
            </div>
          ))}
        </div>

        <h2 style={{ marginTop: 28 }}>What Sofía is doing</h2>
        <div style={{ display: "grid", gap: 10 }}>
          {work.map(([a, b, c]) => (
            <div key={a} style={{ display: "grid", gridTemplateColumns: "120px 1fr 100px", gap: 16, padding: 14, border: "1px solid #ecece7", borderRadius: 14 }}>
              <strong>{a}</strong><span>{b}</span><span style={{ textAlign: "right", opacity: 0.65 }}>{c}</span>
            </div>
          ))}
        </div>
      </section>
    </main>
  );
}
