import { useEffect, useMemo, useRef, useState } from "react";
import { apiCall } from "./lib/api";

const DEFAULT_BASE = "http://localhost:8000";
const LOG_LIMIT = 80;

const emptyStats = {
  tokens_won_today: "-",
  tokens_remaining_today: "-",
  total_usd_balance: "-"
};

function prettyJson(value) {
  try {
    return JSON.stringify(value, null, 2);
  } catch (error) {
    return String(value);
  }
}

function formatCurrency(value) {
  if (value === "-" || value === null || value === undefined) {
    return "-";
  }
  const number = Number(value);
  if (Number.isNaN(number)) {
    return String(value);
  }
  return `$${number.toFixed(2)}`;
}

function formatToken(value) {
  if (value === "-" || value === null || value === undefined) {
    return "-";
  }
  return String(value);
}

function toIsoNow() {
  return new Date().toISOString();
}

export default function App() {
  const [baseUrl, setBaseUrl] = useState(
    localStorage.getItem("dl_api_base") || DEFAULT_BASE
  );
  const [userId, setUserId] = useState("");
  const [amount, setAmount] = useState("1");
  const [wonAt, setWonAt] = useState(toIsoNow());
  const [idempotencyKey, setIdempotencyKey] = useState("");
  const [stats, setStats] = useState(emptyStats);
  const [tokenHistory, setTokenHistory] = useState(null);
  const [usdHistory, setUsdHistory] = useState(null);
  const [adminOverview, setAdminOverview] = useState(null);
  const [adminUsers, setAdminUsers] = useState(null);
  const [adminQuery, setAdminQuery] = useState("");
  const [adminLimit, setAdminLimit] = useState("20");
  const [health, setHealth] = useState(null);
  const [lastResponse, setLastResponse] = useState(null);
  const [logs, setLogs] = useState([]);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [busy, setBusy] = useState(false);
  const [activeView, setActiveView] = useState("operator");

  const logId = useRef(1);

  const apiBase = useMemo(() => {
    try {
      return new URL(baseUrl).toString();
    } catch (error) {
      return DEFAULT_BASE;
    }
  }, [baseUrl]);

  useEffect(() => {
    localStorage.setItem("dl_api_base", apiBase);
  }, [apiBase]);

  useEffect(() => {
    if (!autoRefresh || !userId) {
      return undefined;
    }

    const interval = setInterval(() => {
      void fetchStats();
      void fetchTokenHistory();
      void fetchUsdHistory();
    }, 10000);

    return () => clearInterval(interval);
  }, [autoRefresh, userId, apiBase]);

  function addLog(entry) {
    setLogs((prev) => {
      const next = [entry, ...prev];
      return next.slice(0, LOG_LIMIT);
    });
  }

  function generateIdempotency() {
    const value = crypto.randomUUID();
    setIdempotencyKey(value);
  }

  function generateUserId() {
    const value = crypto.randomUUID();
    setUserId(value);
  }

  async function runCall({ label, path, method, query, body, onSuccess }) {
    setBusy(true);
    const result = await apiCall({
      baseUrl: apiBase,
      path,
      method,
      query,
      body
    });

    setBusy(false);
    setLastResponse({
      label,
      result
    });

    addLog({
      id: logId.current++,
      label,
      path,
      method,
      ok: result.ok,
      status: result.status,
      duration: result.duration,
      timestamp: new Date().toLocaleTimeString()
    });

    if (result.ok && onSuccess) {
      onSuccess(result.payload);
    }

    return result;
  }

  async function fetchHealth() {
    return runCall({
      label: "Health",
      path: "/health",
      method: "GET",
      onSuccess: setHealth
    });
  }

  async function seedUser() {
    return runCall({
      label: "Seed user",
      path: "/api/v1/dev/seed-user",
      method: "POST",
      body: null,
      onSuccess: (data) => {
        setUserId(data.id);
      }
    });
  }

  async function fetchStats() {
    if (!userId) {
      return null;
    }

    return runCall({
      label: "Stats",
      path: "/api/v1/stats",
      method: "GET",
      query: { user_id: userId },
      onSuccess: (data) => setStats(data)
    });
  }

  async function fetchTokenHistory() {
    if (!userId) {
      return null;
    }

    return runCall({
      label: "Token history",
      path: "/api/v1/tokens/history",
      method: "GET",
      query: { user_id: userId },
      onSuccess: (data) => setTokenHistory(data)
    });
  }

  async function fetchUsdHistory() {
    if (!userId) {
      return null;
    }

    return runCall({
      label: "USD history",
      path: "/api/v1/usd/history",
      method: "GET",
      query: { user_id: userId },
      onSuccess: (data) => setUsdHistory(data)
    });
  }

  async function fetchAdminOverview() {
    return runCall({
      label: "Owner overview",
      path: "/api/v1/admin/overview",
      method: "GET",
      onSuccess: (data) => setAdminOverview(data)
    });
  }

  async function fetchAdminUsers() {
    return runCall({
      label: "Owner users",
      path: "/api/v1/admin/users",
      method: "GET",
      query: {
        q: adminQuery || undefined,
        limit: adminLimit || undefined
      },
      onSuccess: (data) => setAdminUsers(data)
    });
  }

  async function submitWin() {
    if (!userId) {
      return null;
    }

    const payload = {
      user_id: userId,
      amount: Number(amount),
      won_at: wonAt,
      idempotency_key: idempotencyKey || crypto.randomUUID()
    };

    return runCall({
      label: "Token win",
      path: "/api/v1/tokens/win",
      method: "POST",
      body: payload,
      onSuccess: () => {
        void fetchStats();
        void fetchTokenHistory();
      }
    });
  }

  const lastTokenWins = tokenHistory?.entries || [];
  const lastUsdEntries = usdHistory?.entries || [];

  return (
    <div className="app">
      <div className="glow" />
      <header className="header">
        <div>
          <p className="eyebrow">Dreamland Control Room</p>
          <h1>Realtime Token Ops Dashboard</h1>
          <p className="subtitle">
            Monitor token wins, conversions, and system health in one place.
          </p>
          <div className="tabs">
            <button
              className={`tab ${activeView === "operator" ? "active" : ""}`}
              onClick={() => setActiveView("operator")}
            >
              Operator
            </button>
            <button
              className={`tab ${activeView === "owner" ? "active" : ""}`}
              onClick={() => setActiveView("owner")}
            >
              Owner
            </button>
          </div>
        </div>
        <div className="header-actions">
          <label className="field">
            <span>API base</span>
            <input
              value={baseUrl}
              onChange={(event) => setBaseUrl(event.target.value)}
              placeholder="http://localhost:8000"
            />
          </label>
          <button className="btn" onClick={fetchHealth} disabled={busy}>
            Ping /health
          </button>
        </div>
      </header>

      <main className="grid">
        {activeView === "operator" ? (
          <>
            <section className="panel">
              <h2>Operator Console</h2>
              <div className="stack">
                <label className="field">
                  <span>User UUID</span>
                  <input
                    value={userId}
                    onChange={(event) => setUserId(event.target.value)}
                    placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
                  />
                </label>
                <div className="row">
                  <button className="btn ghost" onClick={generateUserId}>
                    Generate user
                  </button>
                  <button className="btn ghost" onClick={seedUser} disabled={busy}>
                    Seed user (API)
                  </button>
                  <button className="btn ghost" onClick={fetchStats} disabled={busy}>
                    Fetch stats
                  </button>
                </div>
              </div>

              <div className="divider" />

              <h3>Token Win</h3>
              <div className="stack">
                <label className="field">
                  <span>Tokens (1-5)</span>
                  <input
                    value={amount}
                    onChange={(event) => setAmount(event.target.value)}
                    type="number"
                    min="1"
                    max="5"
                  />
                </label>
                <label className="field">
                  <span>won_at (ISO)</span>
                  <input
                    value={wonAt}
                    onChange={(event) => setWonAt(event.target.value)}
                  />
                </label>
                <label className="field">
                  <span>Idempotency key</span>
                  <input
                    value={idempotencyKey}
                    onChange={(event) => setIdempotencyKey(event.target.value)}
                    placeholder="auto-generated if empty"
                  />
                </label>
                <div className="row">
                  <button className="btn ghost" onClick={generateIdempotency}>
                    Generate key
                  </button>
                  <button className="btn" onClick={submitWin} disabled={busy}>
                    Submit win
                  </button>
                </div>
              </div>

              <div className="divider" />

              <h3>History & Stats</h3>
              <div className="stack">
                <div className="row">
                  <button
                    className="btn ghost"
                    onClick={fetchTokenHistory}
                    disabled={busy}
                  >
                    Token history
                  </button>
                  <button
                    className="btn ghost"
                    onClick={fetchUsdHistory}
                    disabled={busy}
                  >
                    USD history
                  </button>
                </div>
                <label className="toggle">
                  <input
                    type="checkbox"
                    checked={autoRefresh}
                    onChange={(event) => setAutoRefresh(event.target.checked)}
                  />
                  <span>Auto-refresh every 10s</span>
                </label>
              </div>
            </section>

            <section className="panel wide">
              <div className="stats">
                <div className="card">
                  <p>Tokens today</p>
                  <h2>{formatToken(stats.tokens_won_today)}</h2>
                  <span>Daily cap remaining: {stats.tokens_remaining_today}</span>
                </div>
                <div className="card">
                  <p>USD balance</p>
                  <h2>{formatCurrency(stats.total_usd_balance)}</h2>
                  <span>Lifetime conversion total</span>
                </div>
                <div className="card">
                  <p>Service health</p>
                  <h2>{health?.status || "-"}</h2>
                  <span>
                    {health ? `${health.env} / ${health.region}` : "No data"}
                  </span>
                </div>
              </div>

              <div className="split">
                <div className="panel nested">
                  <h3>Token Wins Today</h3>
                  <div className="table">
                    <div className="table-head">
                      <span>Time</span>
                      <span>Amount</span>
                      <span>Converted</span>
                    </div>
                    {lastTokenWins.length === 0 ? (
                      <p className="muted">No token wins yet.</p>
                    ) : (
                      lastTokenWins.map((entry) => (
                        <div key={entry.transaction_id} className="table-row">
                          <span>{new Date(entry.won_at).toLocaleTimeString()}</span>
                          <span>{entry.amount}</span>
                          <span>{entry.is_converted ? "Yes" : "No"}</span>
                        </div>
                      ))
                    )}
                  </div>
                </div>

                <div className="panel nested">
                  <h3>USD Conversions</h3>
                  <div className="table">
                    <div className="table-head">
                      <span>Hour</span>
                      <span>USD</span>
                      <span>Tokens</span>
                    </div>
                    {lastUsdEntries.length === 0 ? (
                      <p className="muted">No conversions yet.</p>
                    ) : (
                      lastUsdEntries.map((entry) => (
                        <div key={entry.transaction_id} className="table-row">
                          <span>{entry.hour_bucket || "-"}</span>
                          <span>{formatCurrency(entry.amount_usd)}</span>
                          <span>{entry.source_tokens ?? "-"}</span>
                        </div>
                      ))
                    )}
                  </div>
                </div>
              </div>
            </section>
          </>
        ) : (
          <>
            <section className="panel">
              <h2>Owner Controls</h2>
              <div className="stack">
                <button className="btn ghost" onClick={fetchAdminOverview}>
                  Load overview
                </button>
                <div className="field">
                  <span>Search users</span>
                  <input
                    value={adminQuery}
                    onChange={(event) => setAdminQuery(event.target.value)}
                    placeholder="username, email, or external ID"
                  />
                </div>
                <div className="row">
                  <label className="field">
                    <span>Limit</span>
                    <input
                      value={adminLimit}
                      onChange={(event) => setAdminLimit(event.target.value)}
                      type="number"
                      min="1"
                      max="100"
                    />
                  </label>
                  <button className="btn" onClick={fetchAdminUsers}>
                    Load users
                  </button>
                </div>
              </div>
            </section>

            <section className="panel wide">
              <div className="panel nested">
                <h3>Owner Overview</h3>
                <div className="admin-grid">
                  <div className="mini">
                    <p>Total users</p>
                    <h3>{adminOverview?.total_users ?? "-"}</h3>
                  </div>
                  <div className="mini">
                    <p>Total token wins</p>
                    <h3>{adminOverview?.total_token_wins ?? "-"}</h3>
                  </div>
                  <div className="mini">
                    <p>Tokens issued</p>
                    <h3>{formatToken(adminOverview?.tokens_issued ?? "-")}</h3>
                  </div>
                  <div className="mini">
                    <p>Tokens converted</p>
                    <h3>{formatToken(adminOverview?.tokens_converted ?? "-")}</h3>
                  </div>
                  <div className="mini">
                    <p>USD paid out</p>
                    <h3>{formatCurrency(adminOverview?.usd_paid_out ?? "-")}</h3>
                  </div>
                  <div className="mini">
                    <p>Fees paid</p>
                    <h3>{formatCurrency(adminOverview?.fees_paid ?? "-")}</h3>
                  </div>
                  <div className="mini">
                    <p>Last conversion</p>
                    <h3>
                      {adminOverview?.last_conversion_at
                        ? new Date(adminOverview.last_conversion_at).toLocaleString()
                        : "-"}
                    </h3>
                  </div>
                </div>
              </div>

              <div className="panel nested">
                <div className="row space-between">
                  <h3>Users</h3>
                  <span className="muted">
                    Total: {adminUsers?.total ?? "-"}
                  </span>
                </div>
                <div className="table-scroll">
                  <div className="table wide">
                    <div className="table-head wide">
                      <span>User</span>
                      <span>Email</span>
                      <span>Tokens</span>
                      <span>Region</span>
                      <span>Status</span>
                      <span>Created</span>
                      <span>UUID</span>
                    </div>
                    {adminUsers?.items?.length ? (
                      adminUsers.items.map((user) => (
                        <div key={user.id} className="table-row wide">
                          <span>{user.username}</span>
                          <span>{user.email}</span>
                          <span>{formatToken(user.tokens_won_lifetime)}</span>
                          <span>{user.region}</span>
                          <span className={user.is_active ? "ok" : "bad"}>
                            {user.is_active ? "Active" : "Inactive"}
                          </span>
                          <span>{new Date(user.created_at).toLocaleDateString()}</span>
                          <span className="mono">{user.id}</span>
                        </div>
                      ))
                    ) : (
                      <p className="muted">No users loaded.</p>
                    )}
                  </div>
                </div>
              </div>
            </section>
          </>
        )}
      </main>

      <section className="panel full">
        <div className="split">
          <div className="panel nested">
            <h3>Request Log</h3>
            <div className="log">
              {logs.length === 0 ? (
                <p className="muted">No requests yet.</p>
              ) : (
                logs.map((entry) => (
                  <div key={entry.id} className="log-row">
                    <span className={entry.ok ? "ok" : "bad"}>
                      {entry.ok ? "OK" : "ERR"}
                    </span>
                    <span>{entry.label}</span>
                    <span>{entry.status}</span>
                    <span>{entry.duration}ms</span>
                    <span>{entry.timestamp}</span>
                  </div>
                ))
              )}
            </div>
          </div>
          <div className="panel nested">
            <h3>Last Response</h3>
            <div className="code">
              <pre>
                <code>
                  {lastResponse
                    ? `${lastResponse.label}\n${prettyJson(lastResponse.result.payload)}`
                    : "No responses yet."}
                </code>
              </pre>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
