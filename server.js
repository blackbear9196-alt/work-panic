const express = require("express");
const path = require("path");

const app = express();
app.use(express.json({ limit: "15mb" }));
app.use(express.static(path.join(__dirname, "public")));

const PORT = process.env.PORT || 3000;
const OPENAI_API_KEY = process.env.OPENAI_API_KEY;
const OPENAI_MODEL = process.env.OPENAI_MODEL || "gpt-6-luna";
const SUPABASE_URL = "https://ooaikveptwczdursnpvx.supabase.co";
const SUPABASE_PUBLISHABLE_KEY = process.env.SUPABASE_PUBLISHABLE_KEY;
const STRIPE_SECRET_KEY = process.env.STRIPE_SECRET_KEY;
const STRIPE_PRO_PRICE_ID = process.env.STRIPE_PRO_PRICE_ID || "price_1UWLY3Q71ELmzPZuIl6Y4i2p";
const APP_URL = process.env.APP_URL || "https://work-panic.onrender.com";

async function supabaseRequest(pathname, options = {}) {
  const headers = {
    apikey: SUPABASE_PUBLISHABLE_KEY,
    "Content-Type": "application/json",
    ...(options.headers || {})
  };
  return fetch(`${SUPABASE_URL}${pathname}`, { ...options, headers });
}

async function getUserFromToken(token) {
  const r = await fetch(`${SUPABASE_URL}/auth/v1/user`, {
    headers: { apikey: SUPABASE_PUBLISHABLE_KEY, Authorization: `Bearer ${token}` }
  });
  if (!r.ok) return null;
  return await r.json();
}

async function requireUser(req, res) {
  const auth = req.headers.authorization || "";
  if (!auth.startsWith("Bearer ")) {
    res.status(401).json({ error: "Not authenticated" });
    return null;
  }
  const token = auth.slice(7);
  const user = await getUserFromToken(token);
  if (!user?.id) {
    res.status(401).json({ error: "Invalid session" });
    return null;
  }
  return { user, token };
}

function monthStart() {
  const d = new Date();
  return new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 1)).toISOString().slice(0, 10);
}

async function getUsage(userId, token) {
  const qs = `/rest/v1/usage_monthly?select=*&user_id=eq.${encodeURIComponent(userId)}&month_start=eq.${monthStart()}&limit=1`;
  const r = await supabaseRequest(qs, { headers: { Authorization: `Bearer ${token}` } });
  const text = await r.text();
  if (!r.ok) throw new Error(`Could not read usage: ${text}`);
  const rows = text ? JSON.parse(text) : [];
  return rows[0] || null;
}

async function saveUsage(userId, token, count) {
  const currentMonth = monthStart();
  const payload = {
    user_id: userId,
    month_start: currentMonth,
    analysis_count: count,
    updated_at: new Date().toISOString()
  };

  // Update the existing monthly row when it already exists. This avoids
  // duplicate-key errors on the unique (user_id, month_start) constraint.
  const existing = await supabaseRequest(
    `/rest/v1/usage_monthly?select=id&user_id=eq.${encodeURIComponent(userId)}&month_start=eq.${encodeURIComponent(currentMonth)}&limit=1`,
    { headers: { Authorization: `Bearer ${token}` } }
  );
  const existingText = await existing.text();
  if (!existing.ok) throw new Error(`Could not check usage: ${existingText}`);
  const rows = existingText ? JSON.parse(existingText) : [];

  if (rows.length > 0) {
    const rowId = rows[0].id;
    const updateR = await supabaseRequest(`/rest/v1/usage_monthly?id=eq.${encodeURIComponent(rowId)}`, {
      method: "PATCH",
      headers: {
        Authorization: `Bearer ${token}`,
        Prefer: "return=representation"
      },
      body: JSON.stringify({ analysis_count: count, updated_at: payload.updated_at })
    });
    const updateText = await updateR.text();
    if (!updateR.ok) throw new Error(`Could not update usage: ${updateText}`);
    return updateText ? JSON.parse(updateText) : [];
  }

  const insertR = await supabaseRequest("/rest/v1/usage_monthly", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      Prefer: "return=representation"
    },
    body: JSON.stringify(payload)
  });
  const insertText = await insertR.text();

  // If another request created the row between our check and insert, update
  // that row instead of surfacing a duplicate-key error to the user.
  if (!insertR.ok && insertR.status === 409) {
    const retryR = await supabaseRequest(
      `/rest/v1/usage_monthly?select=id&user_id=eq.${encodeURIComponent(userId)}&month_start=eq.${encodeURIComponent(currentMonth)}&limit=1`,
      { headers: { Authorization: `Bearer ${token}` } }
    );
    const retryText = await retryR.text();
    if (!retryR.ok) throw new Error(`Could not recover usage row: ${retryText}`);
    const retryRows = retryText ? JSON.parse(retryText) : [];
    if (retryRows.length > 0) {
      const retryUpdate = await supabaseRequest(`/rest/v1/usage_monthly?id=eq.${encodeURIComponent(retryRows[0].id)}`, {
        method: "PATCH",
        headers: {
          Authorization: `Bearer ${token}`,
          Prefer: "return=representation"
        },
        body: JSON.stringify({ analysis_count: count, updated_at: payload.updated_at })
      });
      const retryUpdateText = await retryUpdate.text();
      if (!retryUpdate.ok) throw new Error(`Could not update usage after conflict: ${retryUpdateText}`);
      return retryUpdateText ? JSON.parse(retryUpdateText) : [];
    }
  }

  if (!insertR.ok) throw new Error(`Could not save usage: ${insertText}`);
  return insertText ? JSON.parse(insertText) : [];
}

async function stripeRequest(pathname, options = {}) {
  if (!STRIPE_SECRET_KEY) throw new Error("Stripe is not configured on the server yet.");
  const headers = {
    Authorization: `Bearer ${STRIPE_SECRET_KEY}`,
    ...(options.headers || {})
  };
  return fetch(`https://api.stripe.com${pathname}`, { ...options, headers });
}

async function getStoredSubscription(userId, token) {
  const qs = `/rest/v1/subscriptions?select=id,stripe_customer_id,stripe_subscription_id,status,plan,current_period_end&user_id=eq.${encodeURIComponent(userId)}&limit=1`;
  const r = await supabaseRequest(qs, { headers: { Authorization: `Bearer ${token}` } });
  const text = await r.text();
  if (!r.ok) throw new Error(`Could not read subscription: ${text}`);
  const rows = text ? JSON.parse(text) : [];
  return rows[0] || null;
}

async function getProStatus(userId, token) {
  if (!STRIPE_SECRET_KEY) return { pro: false };

  const stored = await getStoredSubscription(userId, token);
  if (!stored?.stripe_subscription_id) return { pro: false };

  const r = await stripeRequest(`/v1/subscriptions/${encodeURIComponent(stored.stripe_subscription_id)}`);
  const data = await r.json();
  if (!r.ok) return { pro: false };

  const pro = data.status === "active" || data.status === "trialing";
  return {
    pro,
    status: data.status || stored.status || null,
    plan: pro ? "pro" : (stored.plan || null),
    current_period_end: data.current_period_end
      ? new Date(data.current_period_end * 1000).toISOString()
      : (stored.current_period_end || null)
  };
}

app.get("/api/config", (req, res) => {
  res.json({
    supabaseUrl: SUPABASE_URL,
    supabasePublishableKey: SUPABASE_PUBLISHABLE_KEY
  });
});

app.get("/api/usage", async (req, res) => {
  try {
    const auth = await requireUser(req, res);
    if (!auth) return;

    const row = await getUsage(auth.user.id, auth.token);
    const subscription = await getProStatus(auth.user.id, auth.token);
    const count = row?.analysis_count || 0;

    if (subscription.pro) {
      return res.json({ count, limit: null, pro: true, plan: "pro" });
    }

    res.json({ count, limit: 10, pro: false, plan: "free" });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.get("/api/history", async (req, res) => {
  try {
    const auth = await requireUser(req, res);
    if (!auth) return;
    const r = await supabaseRequest(
      `/rest/v1/analysis_history?select=*&user_id=eq.${encodeURIComponent(auth.user.id)}&order=created_at.desc&limit=50`,
      { headers: { Authorization: `Bearer ${auth.token}` } }
    );
    const text = await r.text();
    if (!r.ok) throw new Error(text);
    res.json({ items: text ? JSON.parse(text) : [] });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.post("/api/create-checkout-session", async (req, res) => {
  try {
    const auth = await requireUser(req, res);
    if (!auth) return;

    if (!STRIPE_SECRET_KEY) {
      return res.status(500).json({ error: "Stripe is not configured on the server yet." });
    }

    const params = new URLSearchParams();
    params.set("mode", "subscription");
    params.set("line_items[0][price]", STRIPE_PRO_PRICE_ID);
    params.set("line_items[0][quantity]", "1");
    params.set("success_url", `${APP_URL}/?checkout=success&session_id={CHECKOUT_SESSION_ID}`);
    params.set("cancel_url", `${APP_URL}/?checkout=cancelled`);
    params.set("client_reference_id", auth.user.id);
    params.set("customer_email", auth.user.email || "");
    params.set("metadata[user_id]", auth.user.id);
    params.set("subscription_data[metadata][user_id]", auth.user.id);
    params.set("subscription_data[metadata][plan]", "pro");

    const r = await stripeRequest("/v1/checkout/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: params.toString()
    });

    const data = await r.json();
    if (!r.ok) return res.status(500).json({ error: data?.error?.message || "Stripe checkout failed" });

    res.json({ url: data.url });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.post("/api/sync-subscription", async (req, res) => {
  try {
    const auth = await requireUser(req, res);
    if (!auth) return;

    const sessionId = String(req.body?.session_id || "").trim();
    if (!sessionId) return res.status(400).json({ error: "Missing checkout session." });

    const sessionR = await stripeRequest(
      `/v1/checkout/sessions/${encodeURIComponent(sessionId)}?expand[]=subscription&expand[]=customer`
    );
    const session = await sessionR.json();
    if (!sessionR.ok) return res.status(400).json({ error: session?.error?.message || "Could not verify checkout session." });

    const metadataUserId = session?.metadata?.user_id || session?.client_reference_id;
    if (metadataUserId !== auth.user.id) {
      return res.status(403).json({ error: "Checkout session does not belong to this account." });
    }
    if (session.status !== "complete") {
      return res.status(400).json({ error: "Checkout is not complete yet." });
    }

    const sub = typeof session.subscription === "object" ? session.subscription : null;
    if (!sub?.id) return res.status(400).json({ error: "No subscription was created." });

    const existing = await getStoredSubscription(auth.user.id, auth.token);
    if (!existing) {
      const payload = {
        user_id: auth.user.id,
        stripe_customer_id: typeof session.customer === "object" ? session.customer.id : session.customer,
        stripe_subscription_id: sub.id,
        status: sub.status || "active",
        plan: "pro",
        current_period_end: sub.current_period_end ? new Date(sub.current_period_end * 1000).toISOString() : null,
        updated_at: new Date().toISOString()
      };

      const insertR = await supabaseRequest("/rest/v1/subscriptions", {
        method: "POST",
        headers: {
          Authorization: `Bearer ${auth.token}`,
          Prefer: "return=minimal"
        },
        body: JSON.stringify(payload)
      });
      const insertText = await insertR.text();
      if (!insertR.ok) throw new Error(`Could not save subscription: ${insertText}`);
    }

    const status = await getProStatus(auth.user.id, auth.token);
    res.json({ success: true, ...status });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.post("/api/analyze", async (req, res) => {
  try {
    const auth = await requireUser(req, res);
    if (!auth) return;

    const usage = await getUsage(auth.user.id, auth.token);
    const count = usage?.analysis_count || 0;
    const subscription = await getProStatus(auth.user.id, auth.token);

    if (!subscription.pro && count >= 10) {
      return res.status(429).json({
        error: "FREE_LIMIT_REACHED",
        message: "You have used all 10 free analyses this month. Upgrade to PRO for unlimited analysis."
      });
    }

    const { inputText, imageData, inputType = "text" } = req.body || {};
    if (!inputText && !imageData) return res.status(400).json({ error: "No input provided" });
    if (!OPENAI_API_KEY) return res.status(500).json({ error: "OPENAI_API_KEY is not configured." });

    const system = `You are WORK PANIC, an assistant for employees. Analyze the user's work message or screenshot.
Return ONLY valid JSON with this exact shape:
{
  "what_they_want": "short clear explanation",
  "tasks": ["task 1", "task 2"],
  "deadline": "deadline or Not specified",
  "priority": "Low|Medium|High|Urgent",
  "professional_reply": "professional reply",
  "shorter_reply": "short concise reply",
  "confident_reply": "short confident reply"
}
Be accurate. Do not invent facts. If no deadline is stated, say "Not specified".`;

    const content = [];
    if (inputText) content.push({ type: "text", text: inputText });
    if (imageData) content.push({ type: "image_url", image_url: { url: imageData } });

    const response = await fetch("https://api.openai.com/v1/chat/completions", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${OPENAI_API_KEY}`,
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        model: OPENAI_MODEL,
        response_format: { type: "json_object" },
        messages: [
          { role: "system", content: system },
          { role: "user", content }
        ]
      })
    });

    const data = await response.json();
    if (!response.ok) return res.status(500).json({ error: data?.error?.message || "AI analysis failed" });

    const raw = data?.choices?.[0]?.message?.content || "{}";
    const result = JSON.parse(raw);

    const newCount = count + 1;
    await saveUsage(auth.user.id, auth.token, newCount);

    const historyPayload = {
      user_id: auth.user.id,
      input_type: inputType,
      input_text: inputText || null,
      result
    };

    const historyR = await supabaseRequest("/rest/v1/analysis_history", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${auth.token}`,
        Prefer: "return=minimal"
      },
      body: JSON.stringify(historyPayload)
    });
    const historyText = await historyR.text();
    if (!historyR.ok) throw new Error(`Could not save history: ${historyText}`);

    res.json({
      result,
      usage: {
        count: newCount,
        limit: subscription.pro ? null : 10,
        pro: subscription.pro,
        plan: subscription.pro ? "pro" : "free"
      }
    });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.get("/api/health", (req, res) => {
  res.json({ ok: true, service: "work-panic", api: true });
});

// Never return an HTML page for an unknown /api route. This keeps the client
// from trying to parse a platform HTML error page as JSON.
app.use("/api", (req, res) => {
  res.status(404).json({ error: "API_ROUTE_NOT_FOUND", path: req.path });
});

app.listen(PORT, () => console.log(`WORK PANIC listening on ${PORT}`));
